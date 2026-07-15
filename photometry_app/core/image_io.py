from __future__ import annotations

from pathlib import Path
import re

import numpy as np
from astropy.io import fits
from astropy.io.fits import Header
from PIL import Image

try:
    from xisf import XISF
except ImportError:
    XISF = None


_FITS_IMAGE_SUFFIXES = {".fit", ".fits"}
_XISF_IMAGE_SUFFIXES = {".xisf"}
_STANDARD_IMAGE_SUFFIXES = {".tif", ".tiff", ".png", ".jpg", ".jpeg"}
SUPPORTED_IMAGE_SUFFIXES = _FITS_IMAGE_SUFFIXES | _XISF_IMAGE_SUFFIXES | _STANDARD_IMAGE_SUFFIXES
_STRUCTURAL_FITS_KEYWORDS = {
    "SIMPLE",
    "BITPIX",
    "NAXIS",
    "NAXIS1",
    "NAXIS2",
    "NAXIS3",
    "EXTEND",
    "PCOUNT",
    "GCOUNT",
    "CHECKSUM",
    "DATASUM",
}
_INTEGER_PATTERN = re.compile(r"^[+-]?\d+$")
_FLOAT_PATTERN = re.compile(r"^[+-]?(?:\d+\.\d*|\d*\.\d+|\d+)(?:[Ee][+-]?\d+)?$")
_NORMALIZED_XISF_16BIT_SCALE = 65535.0


def is_supported_image_path(path: Path) -> bool:
    return path.suffix.lower() in SUPPORTED_IMAGE_SUFFIXES


def read_header(path: Path) -> Header:
    if path.suffix.lower() in _FITS_IMAGE_SUFFIXES:
        with fits.open(path) as hdul:
            return hdul[0].header.copy()
    if path.suffix.lower() in _STANDARD_IMAGE_SUFFIXES:
        header, _width, _height = _read_standard_image_header_and_shape(path)
        return header
    return _read_xisf_header(path)


def read_header_and_shape(path: Path) -> tuple[Header, int | None, int | None]:
    if path.suffix.lower() in _FITS_IMAGE_SUFFIXES:
        with fits.open(path) as hdul:
            header = hdul[0].header.copy()
            width = int(header.get("NAXIS1", 0)) or None
            height = int(header.get("NAXIS2", 0)) or None
        return header, width, height

    if path.suffix.lower() in _STANDARD_IMAGE_SUFFIXES:
        return _read_standard_image_header_and_shape(path)

    header = _read_xisf_header(path)
    metadata = _read_xisf_metadata(path)
    geometry = metadata.get("geometry", ())
    width = int(geometry[0]) if len(geometry) >= 1 and geometry[0] else None
    height = int(geometry[1]) if len(geometry) >= 2 and geometry[1] else None
    if width is not None:
        header["NAXIS1"] = width
    if height is not None:
        header["NAXIS2"] = height
    return header, width, height


def read_image_data(path: Path, dtype: type[np.floating] | type[float] | None = float) -> np.ndarray:
    if path.suffix.lower() in _FITS_IMAGE_SUFFIXES:
        with fits.open(path) as hdul:
            data = np.asarray(hdul[0].data)
            return data if dtype is None else np.asarray(data, dtype=dtype)

    if path.suffix.lower() in _STANDARD_IMAGE_SUFFIXES:
        image = np.asarray(_read_standard_image(path))
        return image if dtype is None else np.asarray(image, dtype=dtype)

    image = np.asarray(_read_xisf_image(path))
    if image.ndim == 3 and image.shape[-1] == 1:
        image = image[:, :, 0]
    return image if dtype is None else np.asarray(image, dtype=dtype)


def read_photometry_image_data(path: Path, dtype: type[np.floating] | type[float] = float) -> np.ndarray:
    if path.suffix.lower() in _FITS_IMAGE_SUFFIXES | _STANDARD_IMAGE_SUFFIXES:
        return read_image_data(path, dtype=dtype)

    metadata = _read_xisf_metadata(path)
    image = np.asarray(_read_xisf_image(path), dtype=dtype)
    if image.ndim == 3 and image.shape[-1] == 1:
        image = image[:, :, 0]

    scale_factor = photometry_xisf_scale_factor(metadata)
    if scale_factor is not None:
        image = image * dtype(scale_factor)
    return image


def photometry_xisf_scale_factor(metadata: dict) -> float | None:
    sample_format = str(metadata.get("sampleFormat") or "").strip().lower()
    bounds = str(metadata.get("bounds") or "").strip()
    if sample_format.startswith("float") and bounds == "0:1":
        return _NORMALIZED_XISF_16BIT_SCALE
    return None


def write_fits_copy(source_path: Path, destination: Path) -> Path:
    header = _sanitize_header_for_fits(read_header(source_path))
    data = read_image_data(source_path)
    if data.ndim == 3:
        data = _collapse_multichannel_image_for_plate_solve(data)
    if data.ndim != 2:
        raise ValueError(f"Only 2D monochrome images are supported for plate solving: {source_path.name}")
    fits.PrimaryHDU(data=data, header=header).writeto(destination, overwrite=True)
    return destination


def _read_standard_image_header_and_shape(path: Path) -> tuple[Header, int | None, int | None]:
    with Image.open(path) as image:
        width, height = image.size
        bands = tuple(image.getbands())
    header = Header()
    if width > 0:
        header["NAXIS1"] = int(width)
    if height > 0:
        header["NAXIS2"] = int(height)
    if width > 0 and height > 0:
        header["NAXIS"] = 2
    if bands:
        header["IMGMODE"] = "".join(bands)
    return header, (int(width) if width > 0 else None), (int(height) if height > 0 else None)


def _read_standard_image(path: Path) -> np.ndarray:
    with Image.open(path) as image:
        return np.asarray(image.copy())


def _read_xisf_header(path: Path) -> Header:
    metadata = _read_xisf_metadata(path)
    header = Header()
    fits_keywords = metadata.get("FITSKeywords", {})
    for key, entries in fits_keywords.items():
        for entry in entries:
            value = _coerce_fits_keyword_value(entry.get("value"))
            comment = entry.get("comment")
            try:
                if comment:
                    header[key] = (value, comment)
                else:
                    header[key] = value
            except Exception:
                continue
    _apply_xisf_observation_times(header, metadata)
    _apply_xisf_astrometric_solution(header, metadata)
    return header


def _read_xisf_metadata(path: Path) -> dict:
    reader = _open_xisf(path)
    images = reader.get_images_metadata()
    if not images:
        raise ValueError(f"XISF file contains no image blocks: {path}")
    return images[0]


def _read_xisf_image(path: Path) -> np.ndarray:
    reader = _open_xisf(path)
    return reader.read_image(0)


def _open_xisf(path: Path) -> XISF:
    if XISF is None:
        raise RuntimeError("XISF support requires the 'xisf' package to be installed.")
    return XISF(str(path))


def _sanitize_header_for_fits(header: Header) -> Header:
    sanitized = Header()
    for card in header.cards:
        if card.keyword in _STRUCTURAL_FITS_KEYWORDS:
            continue
        try:
            sanitized.append(card)
        except Exception:
            continue
    return sanitized


def _apply_xisf_astrometric_solution(header: Header, metadata: dict) -> None:
    if "CTYPE1" in header and "CTYPE2" in header:
        return

    properties = metadata.get("XISFProperties", {})
    projection_name = _xisf_property_value(properties, "PCL:AstrometricSolution:ProjectionSystem")
    reference_celestial = _xisf_property_value(properties, "PCL:AstrometricSolution:ReferenceCelestialCoordinates")
    reference_image = _xisf_property_value(properties, "PCL:AstrometricSolution:ReferenceImageCoordinates")
    linear_matrix = _xisf_property_value(properties, "PCL:AstrometricSolution:LinearTransformationMatrix")
    observation_ra = _xisf_property_value(properties, "Observation:Center:RA")
    observation_dec = _xisf_property_value(properties, "Observation:Center:Dec")

    if reference_celestial is None and observation_ra is not None and observation_dec is not None:
        reference_celestial = np.asarray([observation_ra, observation_dec], dtype=float)
    if reference_celestial is None or reference_image is None or linear_matrix is None:
        return

    try:
        celestial = np.asarray(reference_celestial, dtype=float).reshape(-1)
        image = np.asarray(reference_image, dtype=float).reshape(-1)
        matrix = np.asarray(linear_matrix, dtype=float).reshape(2, 2)
    except Exception:
        return
    if celestial.size < 2 or image.size < 2:
        return

    projection = _projection_code_from_xisf(projection_name)
    header.setdefault("CTYPE1", f"RA---{projection}")
    header.setdefault("CTYPE2", f"DEC--{projection}")
    header.setdefault("CUNIT1", "deg")
    header.setdefault("CUNIT2", "deg")
    header.setdefault("CRVAL1", float(celestial[0]))
    header.setdefault("CRVAL2", float(celestial[1]))
    header.setdefault("CRPIX1", float(image[0]))
    header.setdefault("CRPIX2", float(image[1]))
    header.setdefault("CD1_1", float(matrix[0, 0]))
    header.setdefault("CD1_2", float(matrix[0, 1]))
    header.setdefault("CD2_1", float(matrix[1, 0]))
    header.setdefault("CD2_2", float(matrix[1, 1]))


def _apply_xisf_observation_times(header: Header, metadata: dict) -> None:
    properties = metadata.get("XISFProperties", {})
    start_time = _xisf_property_value(properties, "Observation:Time:Start")
    end_time = _xisf_property_value(properties, "Observation:Time:End")
    if _header_timestamp_needs_timezone(header.get("DATE-OBS")) and _has_explicit_timezone(start_time):
        header["DATE-OBS"] = str(start_time).strip()
    if _header_timestamp_needs_timezone(header.get("DATE-END")) and _has_explicit_timezone(end_time):
        header["DATE-END"] = str(end_time).strip()


def _header_timestamp_needs_timezone(value: object) -> bool:
    if value is None:
        return True
    text = str(value).strip()
    if not text:
        return True
    return not _has_explicit_timezone(text)


def _has_explicit_timezone(value: object) -> bool:
    if value is None:
        return False
    text = str(value).strip()
    if not text:
        return False
    return bool(re.search(r"(?:Z|[+-]\d{2}:?\d{2})$", text, flags=re.IGNORECASE))


def _xisf_property_value(properties: dict, key: str) -> object | None:
    entry = properties.get(key)
    if not isinstance(entry, dict):
        return None
    return entry.get("value")


def _projection_code_from_xisf(projection_name: object) -> str:
    normalized = str(projection_name or "").strip().lower()
    projection_map = {
        "gnomonic": "TAN",
        "tangent": "TAN",
        "plate carree": "CAR",
        "mercator": "MER",
        "stereographic": "STG",
    }
    return projection_map.get(normalized, "TAN")


def _collapse_multichannel_image_for_plate_solve(data: np.ndarray) -> np.ndarray:
    if data.shape[-1] in {1, 3, 4}:
        return np.asarray(np.mean(data, axis=-1), dtype=data.dtype)
    if data.shape[0] in {1, 3, 4}:
        return np.asarray(np.mean(data, axis=0), dtype=data.dtype)
    raise ValueError("Only grayscale or simple RGB/RGBA images are supported for plate-solve conversion.")


def _coerce_fits_keyword_value(value: object) -> object:
    if not isinstance(value, str):
        return value

    text = value.strip()
    if not text:
        return text

    upper_text = text.upper()
    if upper_text == "T":
        return True
    if upper_text == "F":
        return False
    if _INTEGER_PATTERN.match(text):
        try:
            return int(text)
        except ValueError:
            return text
    if _FLOAT_PATTERN.match(text):
        try:
            return float(text)
        except ValueError:
            return text
    return text