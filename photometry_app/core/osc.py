"""Native CFA preparation for photometry; no demosaicing or image resampling.

Each output pixel is one blue sample or the sum of two green samples in a
2x2 cell. Variances add; saturation is measured before calibration. Products
are ordinary linear FITS with explicit passbands and uncertainty extensions.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import datetime, timedelta, timezone
import hashlib
import json
import math
from pathlib import Path
import re
from zoneinfo import ZoneInfo

import numpy as np
from astropy.io import fits

from photometry_app.core.image_io import is_raw_camera_image_path, read_header, read_image_data

CFA_KEYS = ("BAYERPAT", "BAYERPATN", "CFAPAT")
PATTERNS = ("RGGB", "BGGR", "GRBG", "GBRG")


def _fits_text(value):
    return str(value).encode("ascii", errors="replace").decode("ascii")


@dataclass(frozen=True)
class OscCameraProfile:
    name: str = "Generic OSC"
    pattern: str = "Auto"
    timestamp_timezone: str = "UTC"
    timestamp_reference: str = "start"
    gain: float | None = None  # electrons / ADU, at the acquisition gain/ISO
    read_noise: float | None = None  # electrons RMS
    saturation: float | None = None  # original sensor ADU, including pedestal
    exposure_seconds: float | None = None  # only for missing metadata


@dataclass(frozen=True)
class OscPreparationRequest:
    science_path: Path
    output_directory: Path
    profile: OscCameraProfile = OscCameraProfile()
    bias_path: Path | None = None
    dark_path: Path | None = None
    flat_path: Path | None = None
    dark_flat_path: Path | None = None
    already_calibrated: bool = False
    channels: tuple[str, ...] = ("TG", "TB")


def has_cfa(header) -> bool:
    return any(str(header.get(key, "")).strip() for key in CFA_KEYS)


def cfa_pattern(header, shape, override="Auto") -> str:
    """Return the pattern in numpy row order, after ROI offsets.

    An override describes the first 2x2 block of the actual array and bypasses
    ambiguous manufacturer orientation/offset metadata.
    """
    if override != "Auto":
        if override not in PATTERNS:
            raise ValueError("Choose RGGB, BGGR, GRBG, or GBRG.")
        return override
    declared = {str(header[key]).strip().upper().replace(" ", "") for key in CFA_KEYS if header.get(key)}
    if len(declared) != 1 or next(iter(declared), "") not in PATTERNS:
        raise ValueError("Missing, conflicting, or unsupported Bayer pattern; select the actual array pattern in the camera profile.")
    tile = np.array(list(declared.pop())).reshape(2, 2)
    offsets = []
    for key in ("XBAYROFF", "YBAYROFF"):
        value = float(header.get(key, 0))
        if not math.isfinite(value) or not value.is_integer():
            raise ValueError(f"Invalid {key}; the Bayer offset must be an integer.")
        offsets.append(int(value) % 2)
    xoff, yoff = offsets
    order = str(header.get("ROWORDER", "TOP-DOWN")).strip().upper()
    if order not in {"TOP-DOWN", "BOTTOM-UP"}:
        raise ValueError("Unknown ROWORDER; select the actual array Bayer pattern explicitly.")
    if order == "BOTTOM-UP":
        yoff = (yoff + shape[0] - 1) % 2
    return "".join(tile[(y + yoff) % 2, (x + xoff) % 2] for y in range(2) for x in range(2))


def _positive(value, label, *, zero=False):
    try:
        value = float(value)
    except (TypeError, ValueError):
        raise ValueError(f"Supply {label} in the camera profile or FITS header.") from None
    if not math.isfinite(value) or value < 0 or (value == 0 and not zero):
        raise ValueError(f"Invalid {label}.")
    return value


def _exposure(header, profile):
    return _positive(header.get("EXPTIME", header.get("EXPOSURE", profile.exposure_seconds)), "exposure time (seconds)")


def normalized_start(header, profile, exposure):
    if str(header.get("TIMESYS", "UTC")).strip().upper() not in {"UTC", "LOCAL", ""}:
        raise ValueError("OSC timestamps must use UTC or local civil time; convert other TIMESYS scales before preparation.")
    text = str(header.get("DATE-OBS", "")).strip()
    try:
        stamp = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        raise ValueError("A valid DATE-OBS is required for OSC science frames.") from None
    if stamp.tzinfo is None:
        name = profile.timestamp_timezone
        offset = re.fullmatch(r"UTC([+-])(\d{2}):(\d{2})", name)
        if offset:
            hours, minutes = int(offset[2]), int(offset[3])
            if hours > 23 or minutes > 59:
                raise ValueError("Invalid UTC offset.")
            zone = timezone((1 if offset[1] == "+" else -1) * timedelta(hours=hours, minutes=minutes))
        else:
            zone = ZoneInfo(name)
        local = stamp.replace(tzinfo=zone)
        if local.utcoffset() != stamp.replace(tzinfo=zone, fold=1).utcoffset():
            raise ValueError("Ambiguous or nonexistent local time at a DST transition; supply an explicit UTC offset.")
        if local.astimezone(timezone.utc).astimezone(zone).replace(tzinfo=None) != stamp:
            raise ValueError("Nonexistent local timestamp; supply a valid timestamp with an explicit offset.")
        stamp = local
    stamp = stamp.astimezone(timezone.utc)
    fractions = {"start": 0., "mid": .5, "end": 1.}
    if profile.timestamp_reference not in fractions:
        raise ValueError("Timestamp reference must be start, mid, or end.")
    return stamp - timedelta(seconds=fractions[profile.timestamp_reference] * exposure)


def read_mosaic(path: Path, profile: OscCameraProfile):
    if is_raw_camera_image_path(path):
        from photometry_app.core.image_io import _open_raw_camera_image
        header = read_header(path)
        with _open_raw_camera_image(path) as raw:
            if raw.raw_pattern is None or np.shape(raw.raw_pattern) != (2, 2):
                raise ValueError("Only 2x2 RGB Bayer RAW sensors are supported (not X-Trans or layered sensors).")
            data = np.array(raw.raw_image_visible, dtype=float, copy=True)
            colors = np.asarray(raw.raw_colors_visible)
            desc = raw.color_desc.decode("ascii")
            pattern = "".join(desc[int(v)] for v in colors[:2, :2].flat)
            if pattern not in PATTERNS:
                raise ValueError("RAW sensor does not expose an RGB Bayer array.")
            # Visible-array metadata already accounts for the sensor margins.
            for key in (*CFA_KEYS, "XBAYROFF", "YBAYROFF", "ROWORDER"):
                header.remove(key, ignore_missing=True, remove_all=True)
            header["BAYERPAT"] = pattern
            limits = [float(raw.white_level)]
            limits.extend(float(v) for v in (getattr(raw, "camera_white_level_per_channel", None) or []) if v is not None and v > 0)
            header["SATURATE"] = min(limits)
            metadata = getattr(raw, "metadata", None)
            if metadata is not None and getattr(metadata, "shutter", None):
                header["EXPTIME"] = float(metadata.shutter)
        header["NAXIS"] = 2
        header["NAXIS1"], header["NAXIS2"] = data.shape[1], data.shape[0]
        _raw_observation_metadata(path, header)
    else:
        if path.suffix.lower() not in {".fit", ".fits"}:
            raise ValueError("Native OSC preparation accepts Bayer FITS or Bayer camera RAW; export other formats as unscaled native-ADU FITS first.")
        header = read_header(path)
        data = np.asarray(read_image_data(path), dtype=float)
    if data.ndim != 2 or min(data.shape) < 2:
        raise ValueError(f"{path.name}: a native 2D Bayer mosaic is required.")
    if header.get("OSCCHAN"):
        raise ValueError(f"{path.name}: this is already an extracted channel.")
    return data, header, cfa_pattern(header, data.shape, profile.pattern)


def _raw_observation_metadata(path, header):
    """Recover acquisition metadata when the RAW container exposes EXIF.

    RAW formats without readable EXIF can supply a per-frame JSON sidecar;
    no filesystem modification date is substituted for observation time.
    """
    from PIL import Image
    try:
        with Image.open(path) as preview:
            exif = preview.getexif()
            details = exif.get_ifd(34665)
            date = details.get(36867)
            if date:
                stamp = datetime.strptime(str(date), "%Y:%m:%d %H:%M:%S").isoformat()
                subsecond = str(details.get(37521, "")).strip()
                if subsecond.isdigit():
                    stamp += "." + subsecond
                offset = str(details.get(36881, "")).strip()
                if re.fullmatch(r"[+-]\d{2}:\d{2}", offset):
                    stamp += offset
                header["DATE-OBS"] = stamp
            if details.get(33434) is not None:
                header["EXPTIME"] = float(details[33434])
            if details.get(34855) is not None:
                header["ISOSPEED"] = int(details[34855])
            if exif.get(272):
                header["INSTRUME"] = _fits_text(exif[272])
    except (OSError, ValueError, TypeError, KeyError):
        pass  # Metadata is still required by science/master validation below.
    sidecar = path.with_suffix(path.suffix + ".json")
    if sidecar.exists():
        values = json.loads(sidecar.read_text(encoding="utf-8"))
        allowed = {"DATE-OBS", "EXPTIME", "INSTRUME", "TELESCOP", "OBJECT", "EGAIN", "RDNOISE",
                   "SATURATE", "ISOSPEED", "GAIN", "OFFSET", "READMODE", "CCD-TEMP"}
        if not isinstance(values, dict) or set(values) - allowed:
            raise ValueError(f"{sidecar.name}: expected a JSON object containing only supported acquisition header keys.")
        for key, value in values.items():
            header[key] = _fits_text(value) if isinstance(value, str) else value
        header.add_history(_fits_text(f"Acquisition metadata supplied by {sidecar.name}."))


def read_plane_extensions(path, shape):
    """Read optional native variance and pre-calibration saturation fractions."""
    result = []
    if Path(path).suffix.lower() not in {".fit", ".fits"}:
        return None, None
    with fits.open(path) as hdus:
        for name in ("VARIANCE", "SATFRAC"):
            plane = np.array(hdus[name].data, dtype=float, copy=True) if name in hdus else None
            if plane is not None and (plane.shape != shape or np.any(plane[np.isfinite(plane)] < 0)):
                raise ValueError(f"{path}: invalid {name} extension.")
            result.append(plane)
    return tuple(result)


def _files(path):
    path = Path(path)
    if path.is_file():
        return [path]
    return sorted(p for p in path.rglob("*") if p.is_file() and
                  (p.suffix.lower() in {".fits", ".fit"} or is_raw_camera_image_path(p)))


def _master(path, profile, shape, pattern, label, warnings, science_header):
    if path is None:
        return None
    files = _files(path)
    if not files:
        raise ValueError(f"No {label} frames found.")
    arrays, exposures, variances = [], [], []
    for item in files:
        data, header, layout = read_mosaic(item, profile)
        if data.shape != shape or layout != pattern:
            raise ValueError(f"{label}: shape and Bayer phase must match the science mosaic.")
        for key in ("INSTRUME", "GAIN", "EGAIN", "ISOSPEED", "OFFSET", "READMODE", "XBINNING", "YBINNING"):
            if key in header and key in science_header:
                try:
                    same = math.isclose(float(header[key]), float(science_header[key]), rel_tol=1e-6, abs_tol=1e-6)
                except (TypeError, ValueError):
                    same = str(header[key]).strip().casefold() == str(science_header[key]).strip().casefold()
                if not same:
                    raise ValueError(f"{label}: {key} differs from the science frame.")
        if label in {"dark", "dark-flat"} and header.get("BIASCOR"):
            raise ValueError(f"{label}: supply an uncorrected matched dark including its bias pedestal.")
        if label == "flat" and str(header.get("MASTTYPE", "")).upper() == "FLAT" and np.nanmedian(data) < 10:
            raise ValueError("Supply unnormalized raw flats, not an already normalized flat master.")
        limit = profile.saturation if profile.saturation is not None else header.get("SATURATE", header.get("SATLEVEL"))
        if limit is not None:
            data = np.where(data < .95 * _positive(limit, "master saturation"), data, np.nan)
        arrays.append(data)
        if label != "bias":
            exposures.append(_exposure(header, OscCameraProfile()))
        variances.append(read_plane_extensions(item, shape)[0])
    if exposures and not np.allclose(exposures, exposures[0], rtol=1e-6, atol=1e-6):
        raise ValueError(f"{label}: all frames must have the same exposure time.")
    if len(arrays) == 1:
        master, variance = arrays[0], variances[0]
        if variance is None:
            warnings.append(f"{label} master has no VARIANCE extension; reported errors omit its calibration uncertainty.")
            variance = np.zeros(shape)
    else:
        stack = np.stack(arrays)
        master = np.median(stack, axis=0)
        # Large-sample variance of a median of independent Gaussian samples.
        variance = np.var(stack, axis=0, ddof=1) * math.pi / (2 * len(stack))
        if len(stack) < 5:
            warnings.append(f"Only {len(stack)} {label} frames: master variance estimate is poorly constrained.")
    return master, variance, exposures[0] if exposures else None


def channel_header(header, shape, positions, channel, pattern):
    result = header.copy()
    xoff = float(np.mean([x for y, x in positions]))
    yoff = float(np.mean([y for y, x in positions]))
    # Lookup distortions and alternate WCS need a full coordinate remap; never
    # silently retain one with the old geometry.
    if any(re.match(r"(CPDIS|DP\d|D2IM|DET2IM)", key) or
           re.match(r"(CRPIX\d|CRVAL\d|CTYPE\d|CDELT\d|CD\d_\d|PC\d_\d)[A-Z]$", key) for key in result):
        raise ValueError("Unsupported lookup/alternate WCS: remove it and plate-solve the extracted channels.")
    for axis, offset in ((1, xoff), (2, yoff)):
        key = f"CRPIX{axis}"
        if key in result:
            result[key] = (float(result[key]) + 1 - offset) / 2
    cd = any(f"CD{i}_{j}" in result for i in (1, 2) for j in (1, 2))
    if cd:
        for i in (1, 2):
            for j in (1, 2):
                key = f"CD{i}_{j}"
                if key in result:
                    result[key] = 2 * float(result[key])
    else:
        for axis in (1, 2):
            if f"CTYPE{axis}" in result or f"CDELT{axis}" in result:
                result[f"CDELT{axis}"] = 2 * float(result.get(f"CDELT{axis}", 1.))
    for key in list(result):
        match = re.fullmatch(r"(A|B|AP|BP)_(\d+)_(\d+)", key)
        if match:
            result[key] = float(result[key]) * 2 ** (int(match[2]) + int(match[3]) - 1)
        if key in (*CFA_KEYS, "XBAYROFF", "YBAYROFF", "ROWORDER", "BAYER", "SATURATE", "SATLEVEL", "SATURATI", "DATAMAX", "CHECKSUM", "DATASUM", "BLANK", "BSCALE", "BZERO"):
            result.remove(key, ignore_missing=True, remove_all=True)
    for key in ("PIXSCALE", "SECPIX", "SCALE"):
        if key in result:
            result[key] = 2 * float(result[key])
    from photometry_app.core.local_wcs import _binning_multiplier
    for canonical, aliases, bin_keys in (("XPIXSZ", ("XPIXSZ", "PIXSIZE1", "PIXELX"), ("XBINNING", "CCDXBIN")),
                                          ("YPIXSZ", ("YPIXSZ", "PIXSIZE2", "PIXELY"), ("YBINNING", "CCDYBIN"))):
        source_key = next((key for key in aliases if key in header), None)
        if source_key:
            pitch = 2 * float(header[source_key]) * _binning_multiplier(header, source_key, *bin_keys)
            result[canonical] = (pitch, "Effective channel pixel pitch including binning (um)")
            for alias in aliases[1:]:
                result.remove(alias, ignore_missing=True, remove_all=True)
    for key in ("XBINNING", "YBINNING"):
        result[key] = 2 * int(result.get(key, 1))
    result["NAXIS1"], result["NAXIS2"] = shape[1] // 2, shape[0] // 2
    if header.get("FILTER"):
        result["ORIGFILT"] = str(header["FILTER"])
    result["FILTER"] = channel
    result["OBSTYPE"] = "DSLR"
    result["OSCCHAN"] = channel
    result["OSCPAT"] = pattern
    result["OSCMETH"] = "CFA-SUM"
    result["OSCNPIX"] = len(positions)
    if "RDNOISE" in result:
        result["RDNOISE"] = float(result["RDNOISE"]) * math.sqrt(len(positions))
    result["IMGMODE"] = "MONO"
    result["BUNIT"] = "adu"
    result.add_history("CAst native CFA extraction: no interpolation; G is G1+G2 per 2x2 cell.")
    result.add_history("Last row/column discarded if the mosaic dimensions are odd.")
    return result


def extract_channels(data, variance, saturation, header, pattern, channels=("TG", "TB")):
    height, width = data.shape[0] // 2 * 2, data.shape[1] // 2 * 2
    if not channels or len(set(channels)) != len(channels) or any(c not in {"TG", "TB", "TR"} for c in channels):
        raise ValueError("Choose distinct TG, TB, and/or TR channels.")
    for channel in channels:
        positions = [(i // 2, i % 2) for i, color in enumerate(pattern) if color == channel[-1]]
        samples = [data[y:height:2, x:width:2] for y, x in positions]
        output = np.sum(samples, axis=0)
        var = np.sum([variance[y:height:2, x:width:2] for y, x in positions], axis=0)
        sat = np.max([saturation[y:height:2, x:width:2] for y, x in positions], axis=0)
        invalid = ~np.isfinite(output) | ~np.isfinite(var) | (var < 0) | ~np.isfinite(sat)
        output[invalid] = np.nan
        var[invalid] = np.nan
        yield channel, output, var, sat, channel_header(header, data.shape, positions, channel, pattern)


def prepare_osc_sequence(request: OscPreparationRequest, *, progress=lambda text: None, cancelled=lambda: False):
    source = Path(request.science_path).resolve()
    destination = Path(request.output_directory).resolve()
    if destination == source or (source.is_dir() and source.is_relative_to(destination)):
        raise ValueError("Choose a separate, new output directory.")
    if destination.exists():
        raise ValueError("Output directory already exists; choose a new directory to preserve existing products.")
    files = [p for p in _files(source) if not p.resolve().is_relative_to(destination)]
    calibration_roots = [Path(p).resolve() for p in (request.bias_path, request.dark_path, request.flat_path, request.dark_flat_path) if p]
    files = [p for p in files if not any(p.resolve() == r or p.resolve().is_relative_to(r) for r in calibration_roots)]
    if not files:
        raise ValueError("No native Bayer science images found.")
    if not request.already_calibrated and (request.flat_path is None or (request.bias_path is None and request.dark_path is None)):
        raise ValueError("Raw science preparation requires a flat and a bias or matched-exposure dark (including its bias pedestal).")
    if request.already_calibrated and any(calibration_roots):
        raise ValueError("Already calibrated mode cannot also apply calibration masters.")
    warnings, products, records = [], [], []
    cache = {}
    destination.mkdir(parents=True)
    manifest = destination / "osc-preparation.json"
    profile = request.profile
    document = {"version": 1, "status": "incomplete", "profile": asdict(profile),
                "request": {k: str(v) if isinstance(v, Path) else v for k, v in asdict(request).items() if k != "profile"},
                "warnings": warnings, "frames": records,
                "calibration_inputs": [{"path": str(p.resolve()), "size": p.stat().st_size, "mtime_ns": p.stat().st_mtime_ns}
                                       for root in calibration_roots for p in _files(root)]}
    def save():
        manifest.write_text(json.dumps(document, indent=2), encoding="utf-8")
    save()
    try:
        for index, path in enumerate(files):
            if cancelled():
                raise ValueError("OSC preparation cancelled; completed products remain in the output directory.")
            progress(f"Preparing OSC {index + 1}/{len(files)}: {path.name}")
            data, header, pattern = read_mosaic(path, profile)
            exposure = _exposure(header, profile)
            start = normalized_start(header, profile, exposure)
            variance, sat = read_plane_extensions(path, data.shape)
            if request.already_calibrated:
                if variance is None or sat is None:
                    raise ValueError("Calibrated mosaics require VARIANCE and original-sensor SATFRAC extensions; otherwise prepare the original raw mosaics with masters.")
            else:
                gain = _positive(profile.gain if profile.gain is not None else header.get("EGAIN"), "gain (electrons/ADU)")
                rn = _positive(profile.read_noise if profile.read_noise is not None else header.get("RDNOISE"), "read noise (electrons)", zero=True)
                limit = _positive(profile.saturation if profile.saturation is not None else header.get("SATURATE", header.get("SATLEVEL")), "sensor saturation (ADU)")
                sat = data / limit
                # Negative raw values are invalid, not a negative saturation fraction.
                sat = np.where(data >= 0, sat, np.nan)
                key = (data.shape, pattern, tuple(str(header.get(k, "")) for k in
                       ("INSTRUME", "GAIN", "EGAIN", "ISOSPEED", "OFFSET", "READMODE", "XBINNING", "YBINNING")))
                if key not in cache:
                    cache[key] = [_master(p, profile, data.shape, pattern, label, warnings, header) for p, label in
                                  ((request.bias_path, "bias"), (request.dark_path, "dark"), (request.flat_path, "flat"), (request.dark_flat_path, "dark-flat"))]
                bias, dark, flat, darkflat = cache[key]
                if dark is not None and not math.isclose(exposure, dark[2], rel_tol=1e-6, abs_tol=1e-6):
                    raise ValueError("Dark and science exposure times must match. OSC preparation never scales CMOS darks.")
                if dark is not None:
                    # Matched raw dark includes bias. Do not subtract bias twice.
                    signal = data - dark[0]
                    calibration_variance = dark[1]
                    pedestal = bias[0] if bias is not None else 0.
                    if bias is None:
                        warnings.append("No separate bias: shot noise includes the raw pedestal, providing a conservative overestimate rather than omitting dark-current shot noise.")
                else:
                    signal = data - bias[0]
                    pedestal, calibration_variance = bias[0], bias[1]
                variance = (np.maximum(data - pedestal, 0) / gain + (rn / gain) ** 2 if variance is None else variance) + calibration_variance
                if darkflat is not None:
                    if not math.isclose(flat[2], darkflat[2], rel_tol=1e-6, abs_tol=1e-6):
                        raise ValueError("Dark-flat and flat exposure times must match.")
                    corrected_flat = flat[0] - darkflat[0]
                    flatvar = flat[1] + darkflat[1]
                elif dark is not None and math.isclose(flat[2], dark[2], rel_tol=1e-6, abs_tol=1e-6):
                    corrected_flat, flatvar = flat[0] - dark[0], flat[1] + dark[1]
                elif bias is not None:
                    corrected_flat, flatvar = flat[0] - bias[0], flat[1] + bias[1]
                    warnings.append("Flats were bias-subtracted only; verify that flat-exposure dark current is negligible or supply matched dark-flats.")
                else:
                    raise ValueError("Flat correction needs a bias or a dark-flat at the flat exposure time.")
                normalized, normvar = np.empty(data.shape), np.empty(data.shape)
                for y in range(2):
                    for x in range(2):
                        plane = corrected_flat[y::2, x::2]
                        good = np.isfinite(plane) & (plane > 0)
                        if not np.any(good):
                            raise ValueError("Flat has an empty Bayer phase.")
                        norm = float(np.median(plane[good]))
                        normalized[y::2, x::2] = plane / norm
                        normvar[y::2, x::2] = flatvar[y::2, x::2] / norm**2
                valid = np.isfinite(normalized) & (normalized > .1) & (normalized < 10)
                normalized[~valid] = np.nan
                # Retain negatives. Never clip a calibrated background to zero.
                data = signal / normalized
                variance = variance / normalized**2 + signal**2 * normvar / normalized**4
                header["EGAIN"], header["RDNOISE"], header["OSCSAT"] = gain, rn, limit
            header["DATE-RAW"] = str(header.get("DATE-OBS", ""))
            header["DATE-OBS"] = start.isoformat().replace("+00:00", "Z")
            header["DATE-AVG"] = (start + timedelta(seconds=exposure / 2)).isoformat().replace("+00:00", "Z")
            header["DATE-END"] = (start + timedelta(seconds=exposure)).isoformat().replace("+00:00", "Z")
            if "DATE-BEG" in header:
                header["DATE-BEG"] = header["DATE-OBS"]
            # Remove contradictory legacy time fields instead of leaving a
            # local/end JD alongside a UTC/start DATE-OBS.
            for time_key in ("JD", "MJD", "JD-OBS", "MJD-OBS", "MJD-AVG", "MJD-END", "TIME-OBS", "UTSTART", "UTEND"):
                header.remove(time_key, ignore_missing=True, remove_all=True)
            header["TIMESYS"] = "UTC"
            header["EXPTIME"] = exposure
            header["OSCPROF"] = _fits_text(profile.name)
            header["OSCCAL"] = "EXTERNAL" if request.already_calibrated else "NATIVE"
            header["OSCUNC"] = "PARTIAL" if warnings else "PROPAGATED"
            header["ORIGFILE"] = _fits_text(path.name)
            header.add_history("DATE-OBS normalized to UTC exposure start; DATE-RAW preserves original.")
            # A stable path digest prevents collisions between same-named inputs.
            digest = hashlib.sha256(str(path.resolve()).encode()).hexdigest()[:10]
            header["OSCFRAME"] = digest
            relative = path.parent.relative_to(source) if source.is_dir() else Path()
            folder = destination / "channels" / relative
            folder.mkdir(parents=True, exist_ok=True)
            frame_products = []
            for channel, plane, var, frac, channel_metadata in extract_channels(data, variance, sat, header, pattern, request.channels):
                output = folder / f"{path.stem}_{digest}_{channel}.fits"
                fits.HDUList([fits.PrimaryHDU(plane.astype(np.float32), channel_metadata),
                              fits.ImageHDU(var.astype(np.float32), name="VARIANCE"),
                              fits.ImageHDU(frac.astype(np.float32), name="SATFRAC")]).writeto(output, checksum=True)
                products.append(output)
                frame_products.append(str(output.relative_to(destination)))
            records.append({"source": str(path.resolve()), "size": path.stat().st_size, "mtime_ns": path.stat().st_mtime_ns,
                            "pattern": pattern, "products": frame_products})
            warnings[:] = list(dict.fromkeys(warnings))
            save()
        document["status"] = "complete"
        save()
    except Exception as exc:
        document["error"] = str(exc)
        save()
        raise
    progress(f"Prepared {len(products)} linear channel images. Review {manifest.name} before analysis.")
    return products, warnings
