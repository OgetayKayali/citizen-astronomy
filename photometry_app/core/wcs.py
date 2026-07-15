from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
import hashlib
import json
import time
from pathlib import Path

import numpy as np
import requests
from astropy.coordinates import SkyCoord
from astropy.io.fits import Header
from astropy.io import fits
from astropy.wcs.utils import proj_plane_pixel_scales
from astropy.wcs import WCS
from astropy import units as u

from photometry_app.core.image_io import write_fits_copy
from photometry_app.core.models import PlateSolveResult, SolvedField, WcsStatus


_ASTROMETRY_REQUEST_MAX_ATTEMPTS = 3
_ASTROMETRY_REQUEST_RETRY_DELAYS_SECONDS = (1.0, 2.0)
_DIRECT_ASTROMETRY_UPLOAD_SUFFIXES = {".fit", ".fits", ".jpg", ".jpeg", ".png"}


@dataclass(frozen=True, slots=True)
class AstrometrySolveHints:
    center_ra_deg: float | None = None
    center_dec_deg: float | None = None
    radius_deg: float | None = None
    scale_lower_degwidth: float | None = None
    scale_upper_degwidth: float | None = None
    downsample_factor: int | None = None
    parity: int | None = None


def validate_wcs(header: Header, source_path: Path | None = None) -> tuple[bool, list[str]]:
    normalized_header = _normalize_celestial_wcs_header(header)
    reasons: list[str] = []

    ctype1 = normalized_header.get("CTYPE1")
    ctype2 = normalized_header.get("CTYPE2")
    if not ctype1 or not ctype2:
        reasons.append("Missing CTYPE1/CTYPE2 WCS keywords.")
        return False, reasons

    if "RA" not in str(ctype1).upper() or "DEC" not in str(ctype2).upper():
        reasons.append("CTYPE keywords do not describe celestial RA/DEC axes.")
        return False, reasons

    for keyword in ("CRVAL1", "CRVAL2", "CRPIX1", "CRPIX2"):
        if keyword not in normalized_header:
            reasons.append(f"Missing {keyword} WCS keyword.")

    if reasons:
        return False, reasons

    try:
        wcs = celestial_wcs(normalized_header)
        if wcs.pixel_n_dim < 2 or wcs.world_n_dim < 2:
            reasons.append("WCS is not two-dimensional.")
            return False, reasons
    except Exception as exc:
        reasons.append(f"Astropy could not parse WCS: {exc}")
        return False, reasons

    if source_path is not None and is_pixinsight_staralignment_output(source_path):
        reasons.append(
            "Embedded WCS was ignored because this looks like a PixInsight StarAlignment output (.xdrz sidecar detected), which can leave sky coordinates inconsistent with warped pixels. Re-solve the aligned frame or use the pre-alignment calibrated data."
        )
        return False, reasons

    return True, reasons


def extract_solved_field(header: Header, width: int | None, height: int | None, wcs_path: Path) -> SolvedField | None:
    if width is None or height is None:
        return None

    wcs = celestial_wcs(header)
    center = wcs.pixel_to_world(width / 2, height / 2)
    radius = _estimate_radius_deg(wcs, width, height)
    return SolvedField(
        center_ra_deg=float(center.ra.deg),
        center_dec_deg=float(center.dec.deg),
        radius_deg=radius,
        width=width,
        height=height,
        wcs_path=wcs_path,
    )


class AstrometryNetClient:
    def __init__(self, api_key: str, base_url: str = "https://nova.astrometry.net/api") -> None:
        self._api_key = api_key
        self._base_url = base_url.rstrip("/")
        self._session = self._create_session()
        self._login_token: str | None = None

    def solve_file(
        self,
        fits_path: Path,
        cache_dir: Path,
        timeout_seconds: int = 300,
        hints: AstrometrySolveHints | None = None,
        progress_callback: Callable[[str], None] | None = None,
    ) -> PlateSolveResult:
        cache_dir.mkdir(parents=True, exist_ok=True)
        cache_key = _hash_file(fits_path)
        cache_json_path = cache_dir / f"{cache_key}.json"
        cached = _load_cached_solution(cache_json_path)
        cached = _validated_cached_solution(cached)
        if cached is not None:
            return cached
        cache_json_path.unlink(missing_ok=True)

        self._login()
        prepared_input = _prepare_plate_solve_input(fits_path, cache_dir)
        try:
            submission_id = self._upload_file(prepared_input.path, hints=hints)
            job_id = self._wait_for_job(
                submission_id,
                timeout_seconds=timeout_seconds,
                progress_callback=progress_callback,
            )
            solved_path = cache_dir / f"{cache_key}_solved.fits"
            self._download_solved_fits(job_id, solved_path)
        finally:
            prepared_input.cleanup()

        with fits.open(solved_path) as hdul:
            header = hdul[0].header
            width = int(header.get("NAXIS1", 0)) or None
            height = int(header.get("NAXIS2", 0)) or None
            valid, reasons = validate_wcs(header)
            solved_field = extract_solved_field(header, width, height, solved_path) if valid else None

        result = PlateSolveResult(
            source_path=fits_path,
            status=WcsStatus.SOLVED if solved_field else WcsStatus.UNSOLVED,
            solved_field=solved_field,
            reasons=reasons,
        )
        _store_cached_solution(cache_json_path, result)
        return result

    def _login(self) -> None:
        if self._login_token:
            return

        payload = {"request-json": json.dumps({"apikey": self._api_key})}
        response = self._request_with_retries(
            "login",
            lambda: self._session.post(f"{self._base_url}/login", data=payload, timeout=30),
        )
        data = response.json()
        if data.get("status") != "success":
            raise RuntimeError(f"Astrometry.net login failed: {data}")
        self._login_token = str(data["session"])

    def _upload_file(self, fits_path: Path, hints: AstrometrySolveHints | None = None) -> int:
        request_payload = {
            "publicly_visible": "n",
            "allow_modifications": "d",
            "session": self._login_token,
        }
        if hints is not None:
            if hints.center_ra_deg is not None and hints.center_dec_deg is not None and hints.radius_deg is not None:
                request_payload.update(
                    {
                        "center_ra": hints.center_ra_deg,
                        "center_dec": hints.center_dec_deg,
                        "radius": hints.radius_deg,
                    }
                )
            if hints.scale_lower_degwidth is not None and hints.scale_upper_degwidth is not None:
                request_payload.update(
                    {
                        "scale_units": "degwidth",
                        "scale_type": "ul",
                        "scale_lower": hints.scale_lower_degwidth,
                        "scale_upper": hints.scale_upper_degwidth,
                    }
                )
            if hints.downsample_factor is not None and hints.downsample_factor > 1:
                request_payload["downsample_factor"] = hints.downsample_factor
            if hints.parity is not None:
                request_payload["parity"] = hints.parity
        payload = {"request-json": json.dumps(request_payload)}
        response = self._request_with_retries(
            "upload",
            lambda: self._upload_request(fits_path, payload),
        )
        data = response.json()
        if data.get("status") != "success":
            raise RuntimeError(f"Astrometry.net upload failed: {data}")
        return int(data["subid"])

    def _wait_for_job(
        self,
        submission_id: int,
        timeout_seconds: int,
        progress_callback: Callable[[str], None] | None = None,
    ) -> int:
        deadline = time.time() + timeout_seconds
        while time.time() < deadline:
            if progress_callback is not None:
                remaining_seconds = max(0, int(deadline - time.time()))
                progress_callback(f"Waiting for astrometry.net solve job... ({remaining_seconds}s remaining)")
            response = self._request_with_retries(
                "submission status",
                lambda: self._session.get(f"{self._base_url}/submissions/{submission_id}", timeout=30),
            )
            data = response.json()
            jobs = [job for job in data.get("jobs", []) if job is not None]
            if jobs:
                job_id = int(jobs[0])
                if self._job_succeeded(job_id):
                    return job_id
            time.sleep(5)
        raise TimeoutError("Timed out waiting for astrometry.net solve job to complete.")

    def _job_succeeded(self, job_id: int) -> bool:
        response = self._request_with_retries(
            "job status",
            lambda: self._session.get(f"{self._base_url}/jobs/{job_id}", timeout=30),
        )
        data = response.json()
        status = str(data.get("status", ""))
        if status == "success":
            return True
        if status in {"failure", "error"}:
            raise RuntimeError(f"Astrometry.net job failed: {data}")
        return False

    def _download_solved_fits(self, job_id: int, destination: Path) -> None:
        response = self._request_with_retries(
            "solved FITS download",
            lambda: self._session.get(
                f"https://nova.astrometry.net/new_fits_file/{job_id}",
                headers={"Referer": "https://nova.astrometry.net/api/login"},
                timeout=120,
            ),
        )
        destination.write_bytes(response.content)

    def _create_session(self) -> requests.Session:
        session = requests.Session()
        session.headers.update({"User-Agent": "CitizenPhotometry/0.1"})
        return session

    def _reset_session(self, *, reset_login: bool = False) -> None:
        try:
            self._session.close()
        except Exception:
            pass
        self._session = self._create_session()
        if reset_login:
            self._login_token = None

    def _upload_request(self, fits_path: Path, payload: dict[str, str]) -> requests.Response:
        with fits_path.open("rb") as handle:
            return self._session.post(
                f"{self._base_url}/upload",
                data=payload,
                files={"file": (fits_path.name, handle, "application/fits")},
                headers={"Connection": "close"},
                timeout=120,
            )

    def _request_with_retries(
        self,
        operation: str,
        request_factory,
    ) -> requests.Response:
        last_error: requests.RequestException | None = None
        for attempt in range(1, _ASTROMETRY_REQUEST_MAX_ATTEMPTS + 1):
            try:
                response = request_factory()
                response.raise_for_status()
                return response
            except requests.RequestException as exc:
                last_error = exc
                if attempt >= _ASTROMETRY_REQUEST_MAX_ATTEMPTS:
                    raise RuntimeError(
                        f"Astrometry.net {operation} request failed after {_ASTROMETRY_REQUEST_MAX_ATTEMPTS} attempts: {exc}"
                    ) from exc
                self._reset_session(reset_login=operation == "login")
                delay = _ASTROMETRY_REQUEST_RETRY_DELAYS_SECONDS[min(attempt - 1, len(_ASTROMETRY_REQUEST_RETRY_DELAYS_SECONDS) - 1)]
                time.sleep(delay)
        raise RuntimeError(f"Astrometry.net {operation} request failed: {last_error}")


class _PreparedPlateSolveInput:
    def __init__(self, path: Path, temporary: bool = False) -> None:
        self.path = path
        self._temporary = temporary

    def cleanup(self) -> None:
        if self._temporary:
            self.path.unlink(missing_ok=True)


def _prepare_plate_solve_input(source_path: Path, cache_dir: Path) -> _PreparedPlateSolveInput:
    if source_path.suffix.lower() in _DIRECT_ASTROMETRY_UPLOAD_SUFFIXES:
        return _PreparedPlateSolveInput(source_path)

    temp_path = cache_dir / f"{source_path.stem}_plate_solve.fits"
    write_fits_copy(source_path, temp_path)
    return _PreparedPlateSolveInput(temp_path, temporary=True)


def _estimate_radius_deg(wcs: WCS, width: int, height: int) -> float:
    scales = proj_plane_pixel_scales(wcs) * u.deg
    pixel_scale_deg = float(scales.mean().to_value(u.deg))
    diagonal_pixels = (width ** 2 + height ** 2) ** 0.5
    return max(pixel_scale_deg * diagonal_pixels / 2.0, 0.05)


def is_pixinsight_staralignment_output(source_path: Path) -> bool:
    return source_path.with_suffix(".xdrz").exists()


def infer_astrometry_solve_hints(
    header: Header,
    width: int | None,
    height: int | None,
    source_path: Path | None = None,
) -> AstrometrySolveHints | None:
    normalized_header = _normalize_celestial_wcs_header(header)
    downsample_factor = _recommended_downsample_factor(width, height)
    try:
        wcs = celestial_wcs(normalized_header)
        if wcs.pixel_n_dim < 2 or wcs.world_n_dim < 2 or width is None or height is None:
            if downsample_factor is None:
                return None
            return AstrometrySolveHints(downsample_factor=downsample_factor)

        center = wcs.pixel_to_world(width / 2, height / 2)
        radius_deg = _estimate_radius_deg(wcs, width, height)
        scales = proj_plane_pixel_scales(wcs) * u.deg
        pixel_scale_deg = float(scales.mean().to_value(u.deg))
        image_width_deg = max(pixel_scale_deg * width, 0.01)
        scale_lower = max(0.01, image_width_deg * 0.7)
        scale_upper = max(scale_lower * 1.05, image_width_deg * 1.3)
        parity = _wcs_parity(wcs)
        if source_path is not None and is_pixinsight_staralignment_output(source_path):
            radius_deg = max(radius_deg * 1.5, 0.25)
        return AstrometrySolveHints(
            center_ra_deg=float(center.ra.deg),
            center_dec_deg=float(center.dec.deg),
            radius_deg=radius_deg,
            scale_lower_degwidth=scale_lower,
            scale_upper_degwidth=scale_upper,
            downsample_factor=downsample_factor,
            parity=parity,
        )
    except Exception:
        if downsample_factor is None:
            return None
        return AstrometrySolveHints(downsample_factor=downsample_factor)


def _recommended_downsample_factor(width: int | None, height: int | None) -> int | None:
    if width is None or height is None:
        return None
    longest_edge = max(width, height)
    if longest_edge >= 6000:
        return 4
    if longest_edge >= 2500:
        return 2
    return None


def _wcs_parity(wcs: WCS) -> int | None:
    try:
        matrix = wcs.pixel_scale_matrix
        determinant = float(matrix[0, 0] * matrix[1, 1] - matrix[0, 1] * matrix[1, 0])
    except Exception:
        return None
    if determinant == 0:
        return None
    return 0 if determinant > 0 else 1


def celestial_wcs(header: Header) -> WCS:
    normalized_header = _normalize_celestial_wcs_header(header)
    return WCS(normalized_header, naxis=2)


def scale_wcs_pixel_grid(
    wcs: WCS,
    *,
    source_width: int,
    source_height: int,
    target_width: int,
    target_height: int,
) -> WCS:
    """Remap a WCS from one pixel grid size to another with the same sky footprint."""
    if source_width <= 0 or source_height <= 0 or target_width <= 0 or target_height <= 0:
        return wcs

    scale_x = float(target_width) / float(source_width)
    scale_y = float(target_height) / float(source_height)
    if abs(scale_x - 1.0) < 1.0e-9 and abs(scale_y - 1.0) < 1.0e-9:
        return wcs

    scaled = wcs.deepcopy()
    scaled.wcs.crpix[0] = float(scaled.wcs.crpix[0]) * scale_x
    scaled.wcs.crpix[1] = float(scaled.wcs.crpix[1]) * scale_y
    if scaled.wcs.has_cd():
        cd = np.asarray(scaled.wcs.cd, dtype=float)
        scaled.wcs.cd = np.array(
            [
                [cd[0, 0] / scale_x, cd[0, 1] / scale_x],
                [cd[1, 0] / scale_y, cd[1, 1] / scale_y],
            ],
            dtype=float,
        )
    elif scaled.wcs.has_pc() and scaled.wcs.has_cdelt():
        scaled.wcs.cdelt[0] = float(scaled.wcs.cdelt[0]) / scale_x
        scaled.wcs.cdelt[1] = float(scaled.wcs.cdelt[1]) / scale_y
    return scaled


def _normalize_celestial_wcs_header(header: Header) -> Header:
    normalized = header.copy()
    ctype1 = str(normalized.get("CTYPE1") or "").strip()
    ctype2 = str(normalized.get("CTYPE2") or "").strip()
    if ctype1 and ctype2:
        return normalized
    if not _has_minimum_astrometric_solution(normalized):
        return normalized
    normalized["CTYPE1"] = "RA---TAN"
    normalized["CTYPE2"] = "DEC--TAN"
    if "CUNIT1" not in normalized:
        normalized["CUNIT1"] = "deg"
    if "CUNIT2" not in normalized:
        normalized["CUNIT2"] = "deg"
    return normalized


def _has_minimum_astrometric_solution(header: Header) -> bool:
    required_keywords = ("CRVAL1", "CRVAL2", "CRPIX1", "CRPIX2")
    if any(keyword not in header for keyword in required_keywords):
        return False
    has_cd_matrix = any(keyword in header for keyword in ("CD1_1", "CD1_2", "CD2_1", "CD2_2"))
    has_pc_matrix = any(keyword in header for keyword in ("PC1_1", "PC1_2", "PC2_1", "PC2_2")) and "CDELT1" in header and "CDELT2" in header
    has_cdelt_scale = "CDELT1" in header and "CDELT2" in header
    return has_cd_matrix or has_pc_matrix or has_cdelt_scale


def _hash_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _load_cached_solution(cache_json_path: Path) -> PlateSolveResult | None:
    if not cache_json_path.exists():
        return None

    payload = json.loads(cache_json_path.read_text(encoding="utf-8"))
    solved_field_payload = payload.get("solved_field")
    solved_field = None
    if solved_field_payload:
        solved_field = SolvedField(
            center_ra_deg=solved_field_payload["center_ra_deg"],
            center_dec_deg=solved_field_payload["center_dec_deg"],
            radius_deg=solved_field_payload["radius_deg"],
            width=solved_field_payload["width"],
            height=solved_field_payload["height"],
            wcs_path=Path(solved_field_payload["wcs_path"]),
        )
    return PlateSolveResult(
        source_path=Path(payload["source_path"]),
        status=WcsStatus(payload["status"]),
        solved_field=solved_field,
        reasons=list(payload.get("reasons", [])),
    )


def _validated_cached_solution(result: PlateSolveResult | None) -> PlateSolveResult | None:
    if result is None:
        return None
    if result.status != WcsStatus.SOLVED or result.solved_field is None:
        return None

    solved_path = result.solved_field.wcs_path
    if not solved_path.exists():
        return None

    try:
        with fits.open(solved_path) as hdul:
            header = hdul[0].header
            width = int(header.get("NAXIS1", 0)) or None
            height = int(header.get("NAXIS2", 0)) or None
        valid, reasons = validate_wcs(header)
        if not valid:
            return None
        solved_field = extract_solved_field(header, width, height, solved_path)
        if solved_field is None:
            return None
    except Exception:
        return None

    return PlateSolveResult(
        source_path=result.source_path,
        status=WcsStatus.SOLVED,
        solved_field=solved_field,
        reasons=reasons,
    )


def _store_cached_solution(cache_json_path: Path, result: PlateSolveResult) -> None:
    if result.status != WcsStatus.SOLVED or result.solved_field is None:
        cache_json_path.unlink(missing_ok=True)
        return

    payload = {
        "source_path": str(result.source_path),
        "status": result.status.value,
        "reasons": result.reasons,
        "solved_field": None,
    }
    if result.solved_field is not None:
        payload["solved_field"] = {
            "center_ra_deg": result.solved_field.center_ra_deg,
            "center_dec_deg": result.solved_field.center_dec_deg,
            "radius_deg": result.solved_field.radius_deg,
            "width": result.solved_field.width,
            "height": result.solved_field.height,
            "wcs_path": str(result.solved_field.wcs_path),
        }
    cache_json_path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
