from __future__ import annotations

from collections import OrderedDict
from concurrent.futures import Future, ThreadPoolExecutor
from dataclasses import dataclass, field, replace
from datetime import UTC, datetime, timedelta
import hashlib
import importlib
import json
import math
import os
from pathlib import Path
from time import perf_counter
from typing import Callable, Mapping, Protocol, Sequence

import numpy as np
from astropy import units as u
from astropy.coordinates import AltAz, EarthLocation, get_body
from astropy.time import Time
from astropy.utils import iers
from PySide6.QtCore import Qt
from PySide6.QtGui import QImage
from PySide6.QtOpenGL import QOpenGLFunctions_2_0, QOpenGLShader, QOpenGLShaderProgram, QOpenGLTexture

from photometry_app.core.benchmarking import BENCHMARK_ENABLED, get_benchmark_recorder


Vector3 = tuple[float, float, float]
Matrix3 = tuple[Vector3, Vector3, Vector3]
IDENTITY_MATRIX_3: Matrix3 = ((1.0, 0.0, 0.0), (0.0, 1.0, 0.0), (0.0, 0.0, 1.0))
MOON_LANDMARK_UVS: tuple[tuple[str, float, float], ...] = (
    ("Tycho", (180.0 - 11.36) / 360.0, (90.0 + 43.31) / 180.0),
    ("Copernicus", (180.0 - 20.08) / 360.0, (90.0 - 9.62) / 180.0),
    ("Mare Crisium", (180.0 + 59.10) / 360.0, (90.0 - 17.00) / 180.0),
    ("Mare Imbrium", (180.0 - 15.00) / 360.0, (90.0 - 32.80) / 180.0),
    ("Plato", (180.0 - 9.30) / 360.0, (90.0 - 51.60) / 180.0),
)


@dataclass(frozen=True, slots=True)
class MoonState:

    observation_time_utc: datetime
    observer_latitude_deg: float
    observer_longitude_deg: float
    observer_elevation_m: float
    ra_deg: float
    dec_deg: float
    altitude_deg: float
    azimuth_deg: float
    distance_km: float
    angular_diameter_deg: float
    phase_angle_deg: float
    illuminated_fraction: float
    moon_direction_equatorial: Vector3
    moon_to_sun_direction_equatorial: Vector3
    libration_longitude_rad: float = 0.0
    libration_latitude_rad: float = 0.0
    lunar_north_position_angle_rad: float = 0.0
    parallactic_angle_rad: float = 0.0
    moon_to_equatorial_matrix: Matrix3 = IDENTITY_MATRIX_3
    equatorial_to_moon_matrix: Matrix3 = IDENTITY_MATRIX_3
    view_to_moon_matrix: Matrix3 = IDENTITY_MATRIX_3
    moon_to_view_matrix: Matrix3 = IDENTITY_MATRIX_3
    sun_dir_moon_local: Vector3 = (0.0, 0.0, 1.0)
    observer_dir_moon_local: Vector3 = (0.0, 0.0, 1.0)
    earth_dir_moon_local: Vector3 = (0.0, 0.0, 1.0)


@dataclass(frozen=True, slots=True)
class LunarOrientationState:

    libration_longitude_rad: float
    libration_latitude_rad: float
    lunar_north_position_angle_rad: float
    parallactic_angle_rad: float
    moon_to_equatorial_matrix: Matrix3
    equatorial_to_moon_matrix: Matrix3
    view_to_moon_matrix: Matrix3
    moon_to_view_matrix: Matrix3
    sun_dir_moon_local: Vector3
    observer_dir_moon_local: Vector3
    earth_dir_moon_local: Vector3


class LunarOrientationProvider(Protocol):

    def compute(
        self,
        observation_time_utc: datetime,
        *,
        ra_deg: float,
        dec_deg: float,
        observer_latitude_deg: float,
        observer_longitude_deg: float,
        moon_direction_equatorial: Vector3,
        moon_to_sun_direction_equatorial: Vector3,
    ) -> LunarOrientationState:
        ...


@dataclass(frozen=True, slots=True)
class LunarOrientationProviderDiagnostics:

    requested_mode: str
    active_provider: str
    spice_available: bool
    spice_kernel_paths: tuple[str, ...]
    loaded_kernel_count: int
    loaded_kernel_paths: tuple[str, ...] = ()
    fallback_reason: str = ""
    diagnostic: str = ""
    kernel_discovery_diagnostic: str = ""

    def as_dict(self) -> dict[str, object]:

        return {
            "requested_mode": self.requested_mode,
            "active_provider": self.active_provider,
            "spice_available": self.spice_available,
            "spice_kernel_paths": list(self.spice_kernel_paths),
            "loaded_kernel_paths": list(self.loaded_kernel_paths),
            "loaded_kernel_count": self.loaded_kernel_count,
            "fallback_reason": self.fallback_reason,
            "diagnostic": self.diagnostic,
            "kernel_discovery_diagnostic": self.kernel_discovery_diagnostic,
        }


@dataclass(frozen=True, slots=True)
class LunarOrientationProviderSelection:

    provider: LunarOrientationProvider
    diagnostics: LunarOrientationProviderDiagnostics


class LunarOrientationSpiceUnavailable(RuntimeError):

    def __init__(self, message: str, *, loaded_kernel_paths: Sequence[Path] = ()) -> None:

        super().__init__(message)
        self.loaded_kernel_paths = tuple(Path(path) for path in loaded_kernel_paths)


class LunarOrientationApprox:

    def compute(
        self,
        observation_time_utc: datetime,
        *,
        ra_deg: float,
        dec_deg: float,
        observer_latitude_deg: float,
        observer_longitude_deg: float,
        moon_direction_equatorial: Vector3,
        moon_to_sun_direction_equatorial: Vector3,
    ) -> LunarOrientationState:

        resolved_time = observation_time_utc.replace(tzinfo=UTC) if observation_time_utc.tzinfo is None else observation_time_utc.astimezone(UTC)
        days_since_j2000 = Time(resolved_time, scale="utc").tdb.jd - 2451545.0
        pole_ra_rad, pole_dec_rad, prime_meridian_rad = self._iau_lunar_pole_and_prime_meridian(days_since_j2000)
        moon_to_equatorial = self._body_to_equatorial_matrix(pole_ra_rad, pole_dec_rad, prime_meridian_rad)
        equatorial_to_moon = self._transpose(moon_to_equatorial)
        observer_direction_equatorial = self._normalize((-moon_direction_equatorial[0], -moon_direction_equatorial[1], -moon_direction_equatorial[2]))
        observer_dir_moon_local = self._normalize(self._matrix_vector_multiply(equatorial_to_moon, observer_direction_equatorial))
        sun_dir_moon_local = self._normalize(self._matrix_vector_multiply(equatorial_to_moon, moon_to_sun_direction_equatorial))
        libration_longitude_rad = math.atan2(observer_dir_moon_local[0], observer_dir_moon_local[2])
        libration_latitude_rad = math.asin(max(-1.0, min(1.0, observer_dir_moon_local[1])))
        view_to_moon = self._canonical_view_to_moon_matrix(
            float(ra_deg),
            float(dec_deg),
            observer_direction_equatorial,
            equatorial_to_moon,
        )
        moon_to_view = self._transpose(view_to_moon)
        lunar_north_position_angle_rad = self._lunar_north_position_angle(
            float(ra_deg),
            float(dec_deg),
            observer_direction_equatorial,
            moon_to_equatorial,
        )
        parallactic_angle_rad = self._parallactic_angle(
            resolved_time,
            float(ra_deg),
            float(dec_deg),
            float(observer_latitude_deg),
            float(observer_longitude_deg),
        )
        return LunarOrientationState(
            libration_longitude_rad=libration_longitude_rad,
            libration_latitude_rad=libration_latitude_rad,
            lunar_north_position_angle_rad=lunar_north_position_angle_rad,
            parallactic_angle_rad=parallactic_angle_rad,
            moon_to_equatorial_matrix=moon_to_equatorial,
            equatorial_to_moon_matrix=equatorial_to_moon,
            view_to_moon_matrix=view_to_moon,
            moon_to_view_matrix=moon_to_view,
            sun_dir_moon_local=sun_dir_moon_local,
            observer_dir_moon_local=observer_dir_moon_local,
            earth_dir_moon_local=observer_dir_moon_local,
        )

    @classmethod
    def _iau_lunar_pole_and_prime_meridian(cls, days_since_j2000: float) -> tuple[float, float, float]:

        d = float(days_since_j2000)
        t = d / 36525.0
        e1 = cls._deg_to_rad(125.045 - 0.0529921 * d)
        e2 = cls._deg_to_rad(250.089 - 0.1059842 * d)
        e3 = cls._deg_to_rad(260.008 + 13.0120009 * d)
        e4 = cls._deg_to_rad(176.625 + 13.3407154 * d)
        e5 = cls._deg_to_rad(357.529 + 0.9856003 * d)
        e6 = cls._deg_to_rad(311.589 + 26.4057084 * d)
        e7 = cls._deg_to_rad(134.963 + 13.0649930 * d)
        e8 = cls._deg_to_rad(276.617 + 0.3287146 * d)
        e9 = cls._deg_to_rad(34.226 + 1.7484877 * d)
        e10 = cls._deg_to_rad(15.134 - 0.1589763 * d)
        e11 = cls._deg_to_rad(119.743 + 0.0036096 * d)
        e12 = cls._deg_to_rad(239.961 + 0.1643573 * d)
        e13 = cls._deg_to_rad(25.053 + 12.9590088 * d)
        ra_deg = (
            269.9949
            + 0.0031 * t
            - 3.8787 * math.sin(e1)
            - 0.1204 * math.sin(e2)
            + 0.0700 * math.sin(e3)
            - 0.0172 * math.sin(e4)
            + 0.0072 * math.sin(e6)
            - 0.0052 * math.sin(e10)
            + 0.0043 * math.sin(e13)
        )
        dec_deg = (
            66.5392
            + 0.0130 * t
            + 1.5419 * math.cos(e1)
            + 0.0239 * math.cos(e2)
            - 0.0278 * math.cos(e3)
            + 0.0068 * math.cos(e4)
            - 0.0029 * math.cos(e6)
            + 0.0009 * math.cos(e7)
            + 0.0008 * math.cos(e10)
            - 0.0009 * math.cos(e13)
        )
        w_deg = (
            38.3213
            + 13.17635815 * d
            - 1.4e-12 * d * d
            + 3.5610 * math.sin(e1)
            + 0.1208 * math.sin(e2)
            - 0.0642 * math.sin(e3)
            + 0.0158 * math.sin(e4)
            + 0.0252 * math.sin(e5)
            - 0.0066 * math.sin(e6)
            - 0.0047 * math.sin(e7)
            - 0.0046 * math.sin(e8)
            + 0.0028 * math.sin(e9)
            + 0.0052 * math.sin(e10)
            + 0.0040 * math.sin(e11)
            + 0.0019 * math.sin(e12)
            - 0.0044 * math.sin(e13)
        )
        return cls._deg_to_rad(ra_deg), cls._deg_to_rad(dec_deg), cls._deg_to_rad(w_deg)

    @classmethod
    def _body_to_equatorial_matrix(cls, pole_ra_rad: float, pole_dec_rad: float, prime_meridian_rad: float) -> Matrix3:

        sin_ra = math.sin(pole_ra_rad)
        cos_ra = math.cos(pole_ra_rad)
        sin_dec = math.sin(pole_dec_rad)
        cos_dec = math.cos(pole_dec_rad)
        sin_w = math.sin(prime_meridian_rad)
        cos_w = math.cos(prime_meridian_rad)
        x_axis = cls._normalize(
            (
                -sin_w * sin_ra - cos_w * sin_dec * cos_ra,
                sin_w * cos_ra - cos_w * sin_dec * sin_ra,
                cos_w * cos_dec,
            )
        )
        y_axis = cls._normalize(
            (
                cos_w * sin_ra - sin_w * sin_dec * cos_ra,
                -cos_w * cos_ra - sin_w * sin_dec * sin_ra,
                sin_w * cos_dec,
            )
        )
        z_axis = cls._normalize((cos_dec * cos_ra, cos_dec * sin_ra, sin_dec))
        return (
            (x_axis[0], y_axis[0], z_axis[0]),
            (x_axis[1], y_axis[1], z_axis[1]),
            (x_axis[2], y_axis[2], z_axis[2]),
        )

    @classmethod
    def _canonical_view_to_moon_matrix(
        cls,
        ra_deg: float,
        dec_deg: float,
        observer_direction_equatorial: Vector3,
        equatorial_to_moon: Matrix3,
    ) -> Matrix3:

        east_axis, north_axis = cls._sky_tangent_axes(ra_deg, dec_deg, observer_direction_equatorial)
        view_axes = (east_axis, north_axis, observer_direction_equatorial)
        local_axes = equatorial_to_moon
        return tuple(
            tuple(cls._dot(view_axis, local_axis) for view_axis in view_axes)  # type: ignore[misc]
            for local_axis in local_axes
        )  # type: ignore[return-value]

    @classmethod
    def _lunar_north_position_angle(
        cls,
        ra_deg: float,
        dec_deg: float,
        observer_direction_equatorial: Vector3,
        moon_to_equatorial: Matrix3,
    ) -> float:

        east_axis, north_axis = cls._sky_tangent_axes(ra_deg, dec_deg, observer_direction_equatorial)
        lunar_north_axis = cls._matrix_vector_multiply(moon_to_equatorial, (0.0, 1.0, 0.0))
        projected_north = cls._normalize(cls._subtract(lunar_north_axis, cls._scale(observer_direction_equatorial, cls._dot(lunar_north_axis, observer_direction_equatorial))))
        return math.atan2(cls._dot(projected_north, east_axis), cls._dot(projected_north, north_axis))

    @classmethod
    def _sky_tangent_axes(cls, ra_deg: float, dec_deg: float, observer_direction_equatorial: Vector3) -> tuple[Vector3, Vector3]:

        ra_rad = cls._deg_to_rad(ra_deg)
        dec_rad = cls._deg_to_rad(dec_deg)
        east_axis = cls._normalize((-math.sin(ra_rad), math.cos(ra_rad), 0.0))
        north_axis = cls._normalize((-math.cos(ra_rad) * math.sin(dec_rad), -math.sin(ra_rad) * math.sin(dec_rad), math.cos(dec_rad)))
        north_axis = cls._normalize(cls._subtract(north_axis, cls._scale(observer_direction_equatorial, cls._dot(north_axis, observer_direction_equatorial))))
        if cls._length(north_axis) <= 1.0e-9:
            north_axis = cls._normalize(cls._cross(observer_direction_equatorial, east_axis))
        return east_axis, north_axis

    @classmethod
    def _parallactic_angle(
        cls,
        observation_time_utc: datetime,
        ra_deg: float,
        dec_deg: float,
        observer_latitude_deg: float,
        observer_longitude_deg: float,
    ) -> float:

        local_sidereal_time_deg = cls._local_sidereal_time_deg(observation_time_utc, observer_longitude_deg)
        hour_angle_rad = cls._deg_to_rad((local_sidereal_time_deg - ra_deg + 180.0) % 360.0 - 180.0)
        latitude_rad = cls._deg_to_rad(observer_latitude_deg)
        dec_rad = cls._deg_to_rad(dec_deg)
        return math.atan2(
            math.sin(hour_angle_rad),
            math.tan(latitude_rad) * math.cos(dec_rad) - math.sin(dec_rad) * math.cos(hour_angle_rad),
        )

    @staticmethod
    def _local_sidereal_time_deg(observation_time: datetime, longitude_deg: float) -> float:

        utc_time = observation_time.astimezone(UTC)
        year = utc_time.year
        month = utc_time.month
        day = utc_time.day
        hour = utc_time.hour + utc_time.minute / 60.0 + utc_time.second / 3600.0 + utc_time.microsecond / 3_600_000_000.0
        if month <= 2:
            year -= 1
            month += 12
        century = math.floor(year / 100)
        correction = 2 - century + math.floor(century / 4)
        jd0 = math.floor(365.25 * (year + 4716)) + math.floor(30.6001 * (month + 1)) + day + correction - 1524.5
        jd = jd0 + hour / 24.0
        centuries = (jd - 2451545.0) / 36525.0
        gmst = 280.46061837 + 360.98564736629 * (jd - 2451545.0) + 0.000387933 * centuries * centuries - centuries * centuries * centuries / 38710000.0
        return (gmst + float(longitude_deg)) % 360.0

    @staticmethod
    def _deg_to_rad(value_deg: float) -> float:

        return math.radians(float(value_deg) % 360.0)

    @staticmethod
    def _matrix_vector_multiply(matrix: Matrix3, vector: Vector3) -> Vector3:

        return (
            matrix[0][0] * vector[0] + matrix[0][1] * vector[1] + matrix[0][2] * vector[2],
            matrix[1][0] * vector[0] + matrix[1][1] * vector[1] + matrix[1][2] * vector[2],
            matrix[2][0] * vector[0] + matrix[2][1] * vector[1] + matrix[2][2] * vector[2],
        )

    @staticmethod
    def _transpose(matrix: Matrix3) -> Matrix3:

        return (
            (matrix[0][0], matrix[1][0], matrix[2][0]),
            (matrix[0][1], matrix[1][1], matrix[2][1]),
            (matrix[0][2], matrix[1][2], matrix[2][2]),
        )

    @staticmethod
    def _normalize(vector: Vector3) -> Vector3:

        length = LunarOrientationApprox._length(vector)
        if length <= 1.0e-12:
            return (0.0, 0.0, 1.0)
        return (vector[0] / length, vector[1] / length, vector[2] / length)

    @staticmethod
    def _length(vector: Vector3) -> float:

        return math.sqrt(vector[0] * vector[0] + vector[1] * vector[1] + vector[2] * vector[2])

    @staticmethod
    def _dot(lhs: Vector3, rhs: Vector3) -> float:

        return lhs[0] * rhs[0] + lhs[1] * rhs[1] + lhs[2] * rhs[2]

    @staticmethod
    def _cross(lhs: Vector3, rhs: Vector3) -> Vector3:

        return (
            lhs[1] * rhs[2] - lhs[2] * rhs[1],
            lhs[2] * rhs[0] - lhs[0] * rhs[2],
            lhs[0] * rhs[1] - lhs[1] * rhs[0],
        )

    @staticmethod
    def _scale(vector: Vector3, scalar: float) -> Vector3:

        return (vector[0] * scalar, vector[1] * scalar, vector[2] * scalar)

    @staticmethod
    def _subtract(lhs: Vector3, rhs: Vector3) -> Vector3:

        return (lhs[0] - rhs[0], lhs[1] - rhs[1], lhs[2] - rhs[2])


class LunarOrientationSpiceProvider(LunarOrientationApprox):

    FRAME_PRIORITY: tuple[str, ...] = ("MOON_ME", "IAU_MOON")

    def __init__(
        self,
        kernel_paths: Sequence[Path],
        *,
        spice_module: object | None = None,
        frame_priority: Sequence[str] = FRAME_PRIORITY,
        fallback_provider: LunarOrientationProvider | None = None,
    ) -> None:

        resolved_kernel_paths = tuple(Path(path) for path in kernel_paths)
        if not resolved_kernel_paths:
            raise LunarOrientationSpiceUnavailable("No SPICE/NAIF kernels were provided")
        missing_paths = tuple(str(path) for path in resolved_kernel_paths if not path.is_file())
        if missing_paths:
            raise LunarOrientationSpiceUnavailable("Missing SPICE/NAIF kernel paths: " + ", ".join(missing_paths[:4]))
        self._spice = spice_module if spice_module is not None else self._import_spiceypy()
        self.kernel_paths = resolved_kernel_paths
        self.loaded_kernel_count = 0
        self.loaded_kernel_paths: tuple[Path, ...] = ()
        self.active_frame = ""
        self.last_compute_error = ""
        self.compute_fallback_count = 0
        self._fallback_provider = fallback_provider
        self._frame_priority = tuple(str(frame).strip() for frame in frame_priority if str(frame).strip()) or self.FRAME_PRIORITY
        loaded_kernel_paths: list[Path] = []
        try:
            for kernel_path in self.kernel_paths:
                getattr(self._spice, "furnsh")(str(kernel_path))
                loaded_kernel_paths.append(kernel_path)
                self.loaded_kernel_count += 1
        except Exception as exc:
            self.loaded_kernel_paths = tuple(loaded_kernel_paths)
            raise LunarOrientationSpiceUnavailable(
                f"Unable to load SPICE/NAIF kernels: {type(exc).__name__}: {exc}",
                loaded_kernel_paths=self.loaded_kernel_paths,
            ) from exc
        self.loaded_kernel_paths = tuple(loaded_kernel_paths)

    @staticmethod
    def _import_spiceypy() -> object:

        try:
            return importlib.import_module("spiceypy")
        except Exception as exc:
            raise LunarOrientationSpiceUnavailable(f"spiceypy is unavailable: {type(exc).__name__}: {exc}") from exc

    def compute(
        self,
        observation_time_utc: datetime,
        *,
        ra_deg: float,
        dec_deg: float,
        observer_latitude_deg: float,
        observer_longitude_deg: float,
        moon_direction_equatorial: Vector3,
        moon_to_sun_direction_equatorial: Vector3,
    ) -> LunarOrientationState:

        resolved_time = observation_time_utc.replace(tzinfo=UTC) if observation_time_utc.tzinfo is None else observation_time_utc.astimezone(UTC)
        try:
            moon_to_equatorial = self._moon_to_equatorial_matrix(resolved_time)
        except LunarOrientationSpiceUnavailable as exc:
            self.last_compute_error = str(exc)
            self.compute_fallback_count += 1
            if self._fallback_provider is not None:
                return self._fallback_provider.compute(
                    resolved_time,
                    ra_deg=ra_deg,
                    dec_deg=dec_deg,
                    observer_latitude_deg=observer_latitude_deg,
                    observer_longitude_deg=observer_longitude_deg,
                    moon_direction_equatorial=moon_direction_equatorial,
                    moon_to_sun_direction_equatorial=moon_to_sun_direction_equatorial,
                )
            raise
        equatorial_to_moon = self._transpose(moon_to_equatorial)
        observer_direction_equatorial = self._normalize((-moon_direction_equatorial[0], -moon_direction_equatorial[1], -moon_direction_equatorial[2]))
        observer_dir_moon_local = self._normalize(self._matrix_vector_multiply(equatorial_to_moon, observer_direction_equatorial))
        sun_dir_moon_local = self._normalize(self._matrix_vector_multiply(equatorial_to_moon, moon_to_sun_direction_equatorial))
        libration_longitude_rad = math.atan2(observer_dir_moon_local[0], observer_dir_moon_local[2])
        libration_latitude_rad = math.asin(max(-1.0, min(1.0, observer_dir_moon_local[1])))
        view_to_moon = self._canonical_view_to_moon_matrix(
            float(ra_deg),
            float(dec_deg),
            observer_direction_equatorial,
            equatorial_to_moon,
        )
        moon_to_view = self._transpose(view_to_moon)
        lunar_north_position_angle_rad = self._lunar_north_position_angle(
            float(ra_deg),
            float(dec_deg),
            observer_direction_equatorial,
            moon_to_equatorial,
        )
        parallactic_angle_rad = self._parallactic_angle(
            resolved_time,
            float(ra_deg),
            float(dec_deg),
            float(observer_latitude_deg),
            float(observer_longitude_deg),
        )
        return LunarOrientationState(
            libration_longitude_rad=libration_longitude_rad,
            libration_latitude_rad=libration_latitude_rad,
            lunar_north_position_angle_rad=lunar_north_position_angle_rad,
            parallactic_angle_rad=parallactic_angle_rad,
            moon_to_equatorial_matrix=moon_to_equatorial,
            equatorial_to_moon_matrix=equatorial_to_moon,
            view_to_moon_matrix=view_to_moon,
            moon_to_view_matrix=moon_to_view,
            sun_dir_moon_local=sun_dir_moon_local,
            observer_dir_moon_local=observer_dir_moon_local,
            earth_dir_moon_local=observer_dir_moon_local,
        )

    def _moon_to_equatorial_matrix(self, observation_time_utc: datetime) -> Matrix3:

        et_seconds = self._ephemeris_time_seconds(observation_time_utc)
        last_error: Exception | None = None
        for frame_name in self._frame_priority:
            try:
                matrix = getattr(self._spice, "pxform")(frame_name, "J2000", et_seconds)
                self.active_frame = frame_name
                return self._spice_matrix_to_matrix3(matrix)
            except Exception as exc:
                last_error = exc
        if last_error is None:
            raise LunarOrientationSpiceUnavailable("No SPICE lunar frame candidates were configured")
        raise LunarOrientationSpiceUnavailable(f"Unable to compute SPICE Moon orientation: {type(last_error).__name__}: {last_error}") from last_error

    def _ephemeris_time_seconds(self, observation_time_utc: datetime) -> float:

        timestamp = observation_time_utc.strftime("%Y-%m-%dT%H:%M:%S.%f UTC")
        try:
            return float(getattr(self._spice, "str2et")(timestamp))
        except Exception as exc:
            raise LunarOrientationSpiceUnavailable(f"Unable to convert UTC to SPICE ET: {type(exc).__name__}: {exc}") from exc

    @staticmethod
    def _spice_matrix_to_matrix3(matrix: object) -> Matrix3:

        rows = tuple(tuple(float(matrix[row][col]) for col in range(3)) for row in range(3))
        return (rows[0], rows[1], rows[2])


LunarOrientationSpiceReady = LunarOrientationSpiceProvider


MOON_ORIENTATION_PROVIDER_ENV_VAR = "CITIZEN_PHOTOMETRY_MOON_ORIENTATION_PROVIDER"
MOON_SPICE_KERNELS_ENV_VAR = "CITIZEN_PHOTOMETRY_MOON_SPICE_KERNELS"
MOON_SPICE_KERNEL_DIR_ENV_VAR = "CITIZEN_PHOTOMETRY_MOON_SPICE_KERNEL_DIR"
_SPICE_KERNEL_SUFFIXES = {".bpc", ".bc", ".bsp", ".tf", ".tls", ".tpc", ".tsc"}


@dataclass(frozen=True, slots=True)
class LunarOrientationKernelDiscovery:

    kernel_paths: tuple[Path, ...]
    diagnostic: str = ""


def select_lunar_orientation_provider(
    mode: str | None = None,
    *,
    kernel_paths: Sequence[Path] | None = None,
    project_root: Path | None = None,
    spice_module: object | None = None,
) -> LunarOrientationProviderSelection:

    requested_mode = _normalize_lunar_orientation_mode(mode or os.getenv(MOON_ORIENTATION_PROVIDER_ENV_VAR))
    if kernel_paths is None:
        kernel_discovery = _discover_spice_kernel_paths_with_diagnostics(project_root)
        resolved_kernel_paths = kernel_discovery.kernel_paths
    else:
        resolved_kernel_paths = tuple(Path(path) for path in kernel_paths)
        kernel_discovery = LunarOrientationKernelDiscovery(resolved_kernel_paths)
    if requested_mode == "approx":
        return LunarOrientationProviderSelection(
            provider=LunarOrientationApprox(),
            diagnostics=LunarOrientationProviderDiagnostics(
                requested_mode=requested_mode,
                active_provider="approx",
                spice_available=False,
                spice_kernel_paths=tuple(str(path) for path in resolved_kernel_paths),
                loaded_kernel_count=0,
                diagnostic="Approximate lunar orientation forced by configuration.",
                kernel_discovery_diagnostic=kernel_discovery.diagnostic,
            ),
        )
    try:
        spice_provider = LunarOrientationSpiceProvider(resolved_kernel_paths, spice_module=spice_module, fallback_provider=LunarOrientationApprox())
    except LunarOrientationSpiceUnavailable as exc:
        fallback_reason = kernel_discovery.diagnostic or str(exc)
        diagnostic_prefix = "SPICE lunar orientation requested" if requested_mode == "spice" else "SPICE lunar orientation unavailable"
        loaded_kernel_paths = tuple(str(path) for path in exc.loaded_kernel_paths)
        return LunarOrientationProviderSelection(
            provider=LunarOrientationApprox(),
            diagnostics=LunarOrientationProviderDiagnostics(
                requested_mode=requested_mode,
                active_provider="approx",
                spice_available=False,
                spice_kernel_paths=tuple(str(path) for path in resolved_kernel_paths),
                loaded_kernel_count=len(loaded_kernel_paths),
                loaded_kernel_paths=loaded_kernel_paths,
                fallback_reason=fallback_reason,
                diagnostic=f"{diagnostic_prefix}; using approximate fallback. {fallback_reason}",
                kernel_discovery_diagnostic=kernel_discovery.diagnostic,
            ),
        )
    return LunarOrientationProviderSelection(
        provider=spice_provider,
        diagnostics=LunarOrientationProviderDiagnostics(
            requested_mode=requested_mode,
            active_provider="spice",
            spice_available=True,
            spice_kernel_paths=tuple(str(path) for path in resolved_kernel_paths),
            loaded_kernel_count=spice_provider.loaded_kernel_count,
            loaded_kernel_paths=tuple(str(path) for path in spice_provider.loaded_kernel_paths),
            diagnostic="SPICE lunar orientation active.",
            kernel_discovery_diagnostic=kernel_discovery.diagnostic,
        ),
    )


def _normalize_lunar_orientation_mode(value: str | None) -> str:

    normalized = str(value or "auto").strip().casefold()
    return normalized if normalized in {"auto", "approx", "spice"} else "auto"


def _discover_spice_kernel_paths(project_root: Path | None = None) -> tuple[Path, ...]:

    return _discover_spice_kernel_paths_with_diagnostics(project_root).kernel_paths


def _discover_spice_kernel_paths_with_diagnostics(project_root: Path | None = None) -> LunarOrientationKernelDiscovery:

    explicit_paths = _parse_spice_kernel_path_list(os.getenv(MOON_SPICE_KERNELS_ENV_VAR))
    if explicit_paths:
        return LunarOrientationKernelDiscovery(explicit_paths)
    explicit_dir = os.getenv(MOON_SPICE_KERNEL_DIR_ENV_VAR)
    if explicit_dir:
        explicit_directory = Path(explicit_dir)
        if not explicit_directory.is_dir():
            return LunarOrientationKernelDiscovery((), f"SPICE/NAIF kernel directory does not exist: {explicit_directory}")
        paths = _kernel_paths_in_directory(explicit_directory)
        if not paths:
            return LunarOrientationKernelDiscovery((), f"SPICE/NAIF kernel directory contains no supported kernels: {explicit_directory}")
        return LunarOrientationKernelDiscovery(paths)
    root = Path(project_root) if project_root is not None else Path(__file__).resolve().parents[2]
    for candidate_dir in (root / "assets" / "spice", root / "kernels" / "spice", root / "spice", root / "textures" / "spice"):
        paths = _kernel_paths_in_directory(candidate_dir)
        if paths:
            return LunarOrientationKernelDiscovery(paths)
    return LunarOrientationKernelDiscovery(())


def _parse_spice_kernel_path_list(value: str | None) -> tuple[Path, ...]:

    if not value:
        return ()
    return tuple(Path(part.strip()) for part in str(value).split(os.pathsep) if part.strip())


def _kernel_paths_in_directory(directory: Path) -> tuple[Path, ...]:

    if not directory.is_dir():
        return ()
    return tuple(sorted(path for path in directory.rglob("*") if path.is_file() and path.suffix.casefold() in _SPICE_KERNEL_SUFFIXES))


@dataclass(frozen=True, slots=True)
class MoonTextureLod:

    label: str
    width: int
    height: int
    image: QImage
    cache_key: tuple[object, ...]


@dataclass(frozen=True, slots=True)
class MoonTileKey:

    kind: str
    lod_level: int
    tile_x: int
    tile_y: int


@dataclass(frozen=True, slots=True)
class MoonTileUvBounds:

    u_min: float
    v_min: float
    u_max: float
    v_max: float


@dataclass(frozen=True, slots=True)
class MoonTextureTile:

    key: MoonTileKey
    label: str
    path: Path
    uv_bounds: MoonTileUvBounds
    image: QImage
    cache_key: tuple[object, ...]
    generation_id: str = ""
    source_hash: str = ""
    convention_hash: str = ""

    @property
    def approx_bytes(self) -> int:

        return max(1, int(self.image.width()) * int(self.image.height()) * 4)


@dataclass(frozen=True, slots=True)
class MoonTileShaderBinding:

    albedo_tile: MoonTextureTile
    normal_tile: MoonTextureTile | None = None

    @property
    def key(self) -> MoonTileKey:

        return self.albedo_tile.key

    @property
    def uv_bounds(self) -> MoonTileUvBounds:

        return self.albedo_tile.uv_bounds


@dataclass(frozen=True, slots=True)
class MoonPolarCapBinding:

    projection: str
    latitude_coverage_deg: float
    blend_start_lat_deg: float
    blend_end_lat_deg: float
    texture_size_px: int = 0
    blend_curve_power: float = 1.0
    normal_blend_start_lat_deg: float = 78.0
    normal_blend_end_lat_deg: float = 86.0
    boundary_luma_delta: float | None = None
    boundary_color_delta: float | None = None
    boundary_contrast_delta: float | None = None
    core_sharpness_estimate: float | None = None
    core_vs_surrounding_detail_delta: float | None = None
    filtering_policy: str = "linear/linear_mipmap_linear; clamp_to_edge"
    footprint_risk: bool = False
    cap_metrics: Mapping[str, Mapping[str, object]] = field(default_factory=dict)
    north_albedo: MoonTextureLod | None = None
    south_albedo: MoonTextureLod | None = None
    north_normal: MoonTextureLod | None = None
    south_normal: MoonTextureLod | None = None
    manifest_hash: str = ""
    source_hash: str = ""
    generation_id: str = ""


@dataclass(frozen=True, slots=True)
class MoonTileSampleRoute:

    source: str
    tile_key: str = ""
    local_u: float = 0.0
    local_v: float = 0.0
    edge_weight: float = 0.0


@dataclass(frozen=True, slots=True)
class MoonTileSettings:

    activation_diameter_px: float = 900.0
    hysteresis_px: float = 120.0
    max_visible_tiles: int = 24
    selection_grid_samples: int = 11
    cache_budget_bytes: int = 256 * 1024 * 1024
    shader_sampler_limit: int = 6
    tile_edge_fade_fraction: float = 0.035
    enforce_source_registration: bool = True
    allow_unregistered_tiles_debug: bool = False
    require_safe_live_activation: bool = True
    visible_coverage_grid_samples: int = 31
    minimum_visible_coverage: float = 0.999
    live_probe_max_color_delta: float = 4.0 / 255.0
    disable_tiled_albedo: bool = False
    disable_tiled_normals: bool = False
    force_global_moon_texture: bool = False
    live_asset_watch_enabled: bool = False
    live_asset_watch_interval_seconds: float = 2.0
    render_path_budget_seconds: float = 0.050


@dataclass(frozen=True, slots=True)
class _MoonTileGenerationSnapshot:

    signature: tuple[object, ...]
    manifest: Mapping[str, object]
    manifest_hash: str
    available_tiles: Mapping[MoonTileKey, Path]
    global_source_id: str
    tile_source_id: str
    tile_convention_hash: str
    detected_longitude_shift_degrees: float | None
    registration_flip_u: bool
    registration_flip_v: bool
    tile_registration_status: str
    tile_registered_to_global: bool
    tiles_rejected_due_to_registration_mismatch: bool
    registration_warning: str


@dataclass(frozen=True, slots=True)
class MoonTileDiagnostics:

    tiled_mode_active: bool
    tile_lod_level: int
    requested_tile_count: int
    resident_tile_count: int
    missing_tile_count: int
    fallback_reason: str
    tile_cache_memory_estimate: int
    pending_tile_count: int = 0
    lower_resolution_tile_count: int = 0
    available_tile_count: int = 0
    visible_tile_count: int = 0
    selected_tile_count: int = 0
    selected_tile_keys: tuple[str, ...] = ()
    tiled_shader_active: bool = False
    bound_tile_count: int = 0
    bound_albedo_tile_count: int = 0
    bound_normal_tile_count: int = 0
    shader_tile_fallback_reason: str = "tiled_mode_inactive"
    tile_edge_fade_enabled: bool = True
    tile_sampler_limit: int = 6
    max_tile_samplers: int = 6
    visible_coverage_estimate: float = 0.0
    fallback_coverage_estimate: float = 1.0
    selected_bound_tile_keys: tuple[str, ...] = ()
    global_albedo_active: bool = False
    global_normal_active: bool = False
    tiled_albedo_active: bool = False
    tiled_normal_active: bool = False
    tiled_normal_disabled_fallback: bool = False
    active_source_mode: str = "global"
    global_source_id: str = ""
    tile_source_id: str = ""
    tile_registration_status: str = "not_evaluated"
    tile_registered_to_global: bool = False
    detected_longitude_shift_degrees: float | None = None
    flip_u: bool = False
    flip_v: bool = False
    tiles_rejected_due_to_registration_mismatch: bool = False
    registration_warning: str = ""
    activation_warning: str = ""
    tile_root_path: str = ""
    manifest_hash: str = ""
    tile_convention_hash: str = ""
    tile_cache_generation_id: str = ""
    cache_generation_matches_manifest: bool = False
    cache_invalidated_this_frame: bool = False
    bound_tile_source_hash: str = ""
    bound_tile_convention_hash: str = ""
    bound_tile_keys: tuple[str, ...] = ()
    bound_tile_uv_bounds: tuple[str, ...] = ()
    bound_tile_texture_predates_current_manifest: bool = False
    visible_uv_footprint: str = ""
    candidate_visible_coverage_estimate: float = 0.0
    candidate_global_fallback_estimate: float = 1.0
    visible_uv_sample_count: int = 0
    any_out_of_bounds_tile_sampling: bool = False
    live_equivalence_status: str = "not_evaluated"
    live_probe_mean_color_delta: float | None = None
    live_probe_max_color_delta: float | None = None
    live_landmark_sample_deltas: tuple[str, ...] = ()
    tiles_rejected_due_to_unsafe_coverage: bool = False
    tiles_rejected_due_to_live_mismatch: bool = False
    active_normal_source: str = "none"
    tiled_normal_strength: float = 0.0
    tiled_normal_fade_factor: float = 0.0
    terminator_normal_factor: float = 0.0
    problematic_tile_key: str = ""
    max_normal_slope: float | None = None
    normal_artifact_risk: bool = False
    normal_tile_statistics: tuple[dict[str, object], ...] = ()
    artifact_lunar_latitude_deg: float | None = None
    artifact_lunar_longitude_deg: float | None = None
    artifact_disc_position: str = ""
    visible_uv_near_north_pole: bool = False
    visible_uv_near_south_pole: bool = False
    visible_uv_crosses_longitude_seam: bool = False
    visible_pole_proximity: float = 0.0
    visible_seam_proximity: float = 0.0
    polar_normal_fade_factor: float = 0.0
    polar_albedo_fade_factor: float = 0.0
    polar_analytic_normal_active: bool = False
    seam_wrap_active: bool = True
    pole_singularity_guard_active: bool = False
    polar_cap_active: bool = False
    north_polar_cap_available: bool = False
    south_polar_cap_available: bool = False
    polar_cap_normal_available: bool = False
    polar_cap_blend_factor: float = 0.0
    polar_cap_projection: str = ""
    visible_pole_latitude: float | None = None
    polar_cap_fallback_reason: str = ""
    polar_cap_size_px: int = 0
    polar_cap_blend_start_lat_deg: float = 0.0
    polar_cap_blend_end_lat_deg: float = 0.0
    polar_cap_blend_curve_power: float = 1.0
    polar_cap_boundary_luma_delta: float | None = None
    polar_cap_boundary_color_delta: float | None = None
    polar_cap_boundary_contrast_delta: float | None = None
    polar_cap_core_sharpness_estimate: float | None = None
    polar_cap_core_vs_surrounding_detail_delta: float | None = None
    polar_cap_normal_blend_factor: float = 0.0
    polar_cap_filtering: str = ""
    polar_cap_ring_risk: bool = False
    polar_cap_footprint_risk: bool = False

    @classmethod
    def inactive(cls, fallback_reason: str = "not_evaluated") -> "MoonTileDiagnostics":

        return cls(
            tiled_mode_active=False,
            tile_lod_level=-1,
            requested_tile_count=0,
            resident_tile_count=0,
            missing_tile_count=0,
            fallback_reason=fallback_reason,
            tile_cache_memory_estimate=0,
        )

    def as_dict(self) -> dict[str, object]:

        return {
            "tiled_mode_active": self.tiled_mode_active,
            "tile_lod_level": self.tile_lod_level,
            "requested_tile_count": self.requested_tile_count,
            "resident_tile_count": self.resident_tile_count,
            "missing_tile_count": self.missing_tile_count,
            "fallback_reason": self.fallback_reason,
            "tile_cache_memory_estimate": self.tile_cache_memory_estimate,
            "pending_tile_count": self.pending_tile_count,
            "lower_resolution_tile_count": self.lower_resolution_tile_count,
            "available_tile_count": self.available_tile_count,
            "visible_tile_count": self.visible_tile_count,
            "selected_tile_count": self.selected_tile_count,
            "selected_tile_keys": list(self.selected_tile_keys),
            "tiled_shader_active": self.tiled_shader_active,
            "bound_tile_count": self.bound_tile_count,
            "bound_albedo_tile_count": self.bound_albedo_tile_count,
            "bound_normal_tile_count": self.bound_normal_tile_count,
            "shader_tile_fallback_reason": self.shader_tile_fallback_reason,
            "tile_edge_fade_enabled": self.tile_edge_fade_enabled,
            "tile_sampler_limit": self.tile_sampler_limit,
            "max_tile_samplers": self.max_tile_samplers,
            "visible_coverage_estimate": self.visible_coverage_estimate,
            "fallback_coverage_estimate": self.fallback_coverage_estimate,
            "selected_bound_tile_keys": list(self.selected_bound_tile_keys),
            "global_albedo_active": self.global_albedo_active,
            "global_normal_active": self.global_normal_active,
            "tiled_albedo_active": self.tiled_albedo_active,
            "tiled_normal_active": self.tiled_normal_active,
            "tiled_normal_disabled_fallback": self.tiled_normal_disabled_fallback,
            "active_source_mode": self.active_source_mode,
            "global_source_id": self.global_source_id,
            "tile_source_id": self.tile_source_id,
            "tile_registration_status": self.tile_registration_status,
            "tile_registered_to_global": self.tile_registered_to_global,
            "detected_longitude_shift_degrees": self.detected_longitude_shift_degrees,
            "flip_u": self.flip_u,
            "flip_v": self.flip_v,
            "tiles_rejected_due_to_registration_mismatch": self.tiles_rejected_due_to_registration_mismatch,
            "registration_warning": self.registration_warning,
            "activation_warning": self.activation_warning,
            "tile_root_path": self.tile_root_path,
            "manifest_hash": self.manifest_hash,
            "tile_convention_hash": self.tile_convention_hash,
            "tile_cache_generation_id": self.tile_cache_generation_id,
            "cache_generation_matches_manifest": self.cache_generation_matches_manifest,
            "cache_invalidated_this_frame": self.cache_invalidated_this_frame,
            "bound_tile_source_hash": self.bound_tile_source_hash,
            "bound_tile_convention_hash": self.bound_tile_convention_hash,
            "bound_tile_keys": list(self.bound_tile_keys),
            "bound_tile_uv_bounds": list(self.bound_tile_uv_bounds),
            "bound_tile_texture_predates_current_manifest": self.bound_tile_texture_predates_current_manifest,
            "visible_uv_footprint": self.visible_uv_footprint,
            "candidate_visible_coverage_estimate": self.candidate_visible_coverage_estimate,
            "candidate_global_fallback_estimate": self.candidate_global_fallback_estimate,
            "visible_uv_sample_count": self.visible_uv_sample_count,
            "any_out_of_bounds_tile_sampling": self.any_out_of_bounds_tile_sampling,
            "live_equivalence_status": self.live_equivalence_status,
            "live_probe_mean_color_delta": self.live_probe_mean_color_delta,
            "live_probe_max_color_delta": self.live_probe_max_color_delta,
            "live_landmark_sample_deltas": list(self.live_landmark_sample_deltas),
            "tiles_rejected_due_to_unsafe_coverage": self.tiles_rejected_due_to_unsafe_coverage,
            "tiles_rejected_due_to_live_mismatch": self.tiles_rejected_due_to_live_mismatch,
            "active_normal_source": self.active_normal_source,
            "tiled_normal_strength": self.tiled_normal_strength,
            "tiled_normal_fade_factor": self.tiled_normal_fade_factor,
            "terminator_normal_factor": self.terminator_normal_factor,
            "problematic_tile_key": self.problematic_tile_key,
            "max_normal_slope": self.max_normal_slope,
            "normal_artifact_risk": self.normal_artifact_risk,
            "normal_tile_statistics": [dict(item) for item in self.normal_tile_statistics],
            "artifact_lunar_latitude_deg": self.artifact_lunar_latitude_deg,
            "artifact_lunar_longitude_deg": self.artifact_lunar_longitude_deg,
            "artifact_disc_position": self.artifact_disc_position,
            "visible_uv_near_north_pole": self.visible_uv_near_north_pole,
            "visible_uv_near_south_pole": self.visible_uv_near_south_pole,
            "visible_uv_crosses_longitude_seam": self.visible_uv_crosses_longitude_seam,
            "visible_pole_proximity": self.visible_pole_proximity,
            "visible_seam_proximity": self.visible_seam_proximity,
            "polar_normal_fade_factor": self.polar_normal_fade_factor,
            "polar_albedo_fade_factor": self.polar_albedo_fade_factor,
            "polar_analytic_normal_active": self.polar_analytic_normal_active,
            "seam_wrap_active": self.seam_wrap_active,
            "pole_singularity_guard_active": self.pole_singularity_guard_active,
            "polar_cap_active": self.polar_cap_active,
            "north_polar_cap_available": self.north_polar_cap_available,
            "south_polar_cap_available": self.south_polar_cap_available,
            "polar_cap_normal_available": self.polar_cap_normal_available,
            "polar_cap_blend_factor": self.polar_cap_blend_factor,
            "polar_cap_projection": self.polar_cap_projection,
            "visible_pole_latitude": self.visible_pole_latitude,
            "polar_cap_fallback_reason": self.polar_cap_fallback_reason,
            "polar_cap_size_px": self.polar_cap_size_px,
            "polar_cap_blend_start_lat_deg": self.polar_cap_blend_start_lat_deg,
            "polar_cap_blend_end_lat_deg": self.polar_cap_blend_end_lat_deg,
            "polar_cap_blend_curve_power": self.polar_cap_blend_curve_power,
            "polar_cap_boundary_luma_delta": self.polar_cap_boundary_luma_delta,
            "polar_cap_boundary_color_delta": self.polar_cap_boundary_color_delta,
            "polar_cap_boundary_contrast_delta": self.polar_cap_boundary_contrast_delta,
            "polar_cap_core_sharpness_estimate": self.polar_cap_core_sharpness_estimate,
            "polar_cap_core_vs_surrounding_detail_delta": self.polar_cap_core_vs_surrounding_detail_delta,
            "polar_cap_normal_blend_factor": self.polar_cap_normal_blend_factor,
            "polar_cap_filtering": self.polar_cap_filtering,
            "polar_cap_ring_risk": self.polar_cap_ring_risk,
            "polar_cap_footprint_risk": self.polar_cap_footprint_risk,
        }

    def with_render_path_state(
        self,
        *,
        global_albedo_active: bool,
        global_normal_active: bool,
        tile_bindings: Sequence[MoonTileShaderBinding] = (),
        tiled_shader_active: bool | None = None,
        bound_tile_count: int | None = None,
        bound_albedo_tile_count: int | None = None,
        bound_normal_tile_count: int | None = None,
        tiled_normal_strength: float | None = None,
        tiled_normal_fade_factor: float | None = None,
        terminator_normal_factor: float | None = None,
    ) -> "MoonTileDiagnostics":

        resolved_bindings = tuple(tile_bindings)
        resolved_bound_tile_count = len(resolved_bindings) if bound_tile_count is None else int(bound_tile_count)
        resolved_albedo_count = len(resolved_bindings) if bound_albedo_tile_count is None else int(bound_albedo_tile_count)
        resolved_normal_count = sum(1 for binding in resolved_bindings if binding.normal_tile is not None) if bound_normal_tile_count is None else int(bound_normal_tile_count)
        resolved_tiled_shader_active = bool(resolved_bindings) if tiled_shader_active is None else bool(tiled_shader_active)
        resolved_normal_fade_factor = 1.0 if tiled_normal_fade_factor is None else max(0.0, min(1.0, float(tiled_normal_fade_factor)))
        effective_tiled_normal_active = resolved_normal_count > 0 and resolved_normal_fade_factor > 1.0e-6
        active_normal_source = "tiled" if effective_tiled_normal_active else ("global" if global_normal_active else "none")
        resolved_source_mode = "tiled" if resolved_albedo_count > 0 else (
            "fallback"
            if self.tiles_rejected_due_to_registration_mismatch
            or self.tiles_rejected_due_to_unsafe_coverage
            or self.tiles_rejected_due_to_live_mismatch
            else "global"
        )
        return replace(
            self,
            global_albedo_active=bool(global_albedo_active),
            global_normal_active=bool(global_normal_active),
            tiled_albedo_active=resolved_albedo_count > 0,
            tiled_normal_active=effective_tiled_normal_active,
            tiled_normal_disabled_fallback=bool(
                self.tiled_normal_disabled_fallback
                or (resolved_normal_count > 0 and bool(global_normal_active) and not effective_tiled_normal_active)
            ),
            tiled_shader_active=resolved_tiled_shader_active,
            bound_tile_count=resolved_bound_tile_count,
            bound_albedo_tile_count=resolved_albedo_count,
            bound_normal_tile_count=resolved_normal_count,
            visible_coverage_estimate=self.visible_coverage_estimate if resolved_albedo_count > 0 else 0.0,
            fallback_coverage_estimate=1.0 - self.visible_coverage_estimate if resolved_albedo_count > 0 else 1.0,
            active_source_mode=resolved_source_mode,
            active_normal_source=active_normal_source,
            tiled_normal_strength=self.tiled_normal_strength if tiled_normal_strength is None else max(0.0, float(tiled_normal_strength)),
            tiled_normal_fade_factor=self.tiled_normal_fade_factor if tiled_normal_fade_factor is None else resolved_normal_fade_factor,
            terminator_normal_factor=self.terminator_normal_factor if terminator_normal_factor is None else max(0.0, min(1.0, float(terminator_normal_factor))),
        )

    def with_polar_cap_state(self, binding: MoonPolarCapBinding | None) -> "MoonTileDiagnostics":

        visible_pole_latitude = self.artifact_lunar_latitude_deg
        if visible_pole_latitude is None:
            if self.visible_uv_near_south_pole:
                visible_pole_latitude = -90.0
            elif self.visible_uv_near_north_pole:
                visible_pole_latitude = 90.0
        visible_north = bool(self.visible_uv_near_north_pole)
        visible_south = bool(self.visible_uv_near_south_pole)
        active_pole = "north" if visible_north else ("south" if visible_south else "")
        pole_metrics = dict(binding.cap_metrics.get(active_pole, {})) if binding is not None and active_pole else {}
        north_available = binding is not None and binding.north_albedo is not None
        south_available = binding is not None and binding.south_albedo is not None
        appropriate_cap_available = (visible_north and north_available) or (visible_south and south_available)
        blend_factor = (
            MoonRendererGL.polar_cap_blend_factor_for_latitude(
                float(visible_pole_latitude),
                start_lat_deg=None if binding is None else binding.blend_start_lat_deg,
                end_lat_deg=None if binding is None else binding.blend_end_lat_deg,
                curve_power=None if binding is None else binding.blend_curve_power,
            )
            if visible_pole_latitude is not None and appropriate_cap_available
            else 0.0
        )
        cap_active = bool(appropriate_cap_available and blend_factor > 0.0)
        normal_available = bool(
            binding is not None
            and ((visible_north and binding.north_normal is not None) or (visible_south and binding.south_normal is not None))
        )
        normal_blend_factor = (
            MoonRendererGL.polar_cap_normal_blend_factor_for_latitude(
                float(visible_pole_latitude),
                start_lat_deg=None if binding is None else binding.normal_blend_start_lat_deg,
                end_lat_deg=None if binding is None else binding.normal_blend_end_lat_deg,
            )
            if visible_pole_latitude is not None and normal_available
            else 0.0
        )
        if cap_active:
            fallback_reason = ""
        elif binding is None and (visible_north or visible_south):
            fallback_reason = "polar_cap_unavailable_phase5l_fallback"
        elif (visible_north or visible_south) and not appropriate_cap_available:
            fallback_reason = "visible_pole_cap_missing_phase5l_fallback"
        else:
            fallback_reason = "polar_cap_not_visible"
        return replace(
            self,
            polar_cap_active=cap_active,
            north_polar_cap_available=bool(north_available),
            south_polar_cap_available=bool(south_available),
            polar_cap_normal_available=normal_available,
            polar_cap_blend_factor=float(blend_factor),
            polar_cap_projection="" if binding is None else str(binding.projection),
            visible_pole_latitude=visible_pole_latitude,
            polar_cap_fallback_reason=fallback_reason,
            polar_cap_size_px=0 if binding is None else int(binding.texture_size_px),
            polar_cap_blend_start_lat_deg=0.0 if binding is None else float(binding.blend_start_lat_deg),
            polar_cap_blend_end_lat_deg=0.0 if binding is None else float(binding.blend_end_lat_deg),
            polar_cap_blend_curve_power=1.0 if binding is None else float(binding.blend_curve_power),
            polar_cap_boundary_luma_delta=None if binding is None else binding.boundary_luma_delta,
            polar_cap_boundary_color_delta=None if binding is None else binding.boundary_color_delta,
            polar_cap_boundary_contrast_delta=(
                None
                if binding is None
                else (
                    float(pole_metrics["boundary_contrast_delta"])
                    if pole_metrics.get("boundary_contrast_delta") is not None
                    else binding.boundary_contrast_delta
                )
            ),
            polar_cap_core_sharpness_estimate=(
                None
                if binding is None
                else (
                    float(pole_metrics["core_sharpness_estimate"])
                    if pole_metrics.get("core_sharpness_estimate") is not None
                    else binding.core_sharpness_estimate
                )
            ),
            polar_cap_core_vs_surrounding_detail_delta=(
                None
                if binding is None
                else (
                    float(pole_metrics["core_vs_surrounding_detail_delta"])
                    if pole_metrics.get("core_vs_surrounding_detail_delta") is not None
                    else binding.core_vs_surrounding_detail_delta
                )
            ),
            polar_cap_normal_blend_factor=float(normal_blend_factor),
            polar_cap_filtering="" if binding is None else str(binding.filtering_policy),
            polar_cap_ring_risk=bool(
                binding is not None
                and (
                    bool(pole_metrics.get("footprint_risk", binding.footprint_risk))
                    or (
                        cap_active
                        and (
                            binding.texture_size_px < 1024
                            or float(binding.boundary_luma_delta or 0.0) > 0.02
                            or float(binding.boundary_color_delta or 0.0) > 0.02
                        )
                    )
                )
            ),
            polar_cap_footprint_risk=bool(binding is not None and pole_metrics.get("footprint_risk", binding.footprint_risk)),
        )


@dataclass(frozen=True, slots=True)
class MoonVisualSettings:

    # Soften the analytical phase boundary slightly so the lit limb avoids a harsh CG edge.
    terminator_soft_start: float = -0.08
    terminator_soft_end: float = 0.12
    # Restrict terrain-normal relief mostly to the terminator where crater relief reads naturally.
    terrain_terminator_start: float = -0.18
    terrain_terminator_peak_end: float = 0.24
    terrain_terminator_fade_start: float = 0.32
    terrain_terminator_fade_end: float = 0.78
    terrain_blend_cap: float = 0.64
    terrain_normal_strength_global: float = 0.055
    terrain_normal_strength_terminator: float = 0.34
    terrain_normal_strength_global_exaggerated: float = 0.16
    terrain_normal_strength_terminator_exaggerated: float = 0.62
    # Tiled relief is independently controlled and enters gently after albedo tiles activate.
    global_normal_strength: float = 0.055
    tiled_normal_strength: float = 0.036
    global_terminator_normal_strength: float = 0.34
    tiled_terminator_normal_strength: float = 0.18
    tiled_normal_max_slope: float = 0.34
    tiled_normal_fade_start_offset_px: float = 120.0
    tiled_normal_fade_end_offset_px: float = 620.0
    terminator_normal_low_light_start: float = -0.04
    terminator_normal_low_light_end: float = 0.14
    terminator_normal_min_factor: float = 0.12
    # Equirectangular longitude degenerates at the poles; preserve geography while softening that singularity.
    polar_normal_fade_start_lat_deg: float = 70.0
    polar_normal_fade_end_lat_deg: float = 86.0
    polar_albedo_fade_start_lat_deg: float = 82.0
    polar_albedo_fade_end_lat_deg: float = 89.5
    polar_use_analytic_normal: bool = True
    seam_diagnostic_width_uv: float = 0.025
    polar_cap_normal_strength: float = 0.014
    polar_cap_blend_start_lat_deg: float = 60.0
    polar_cap_blend_end_lat_deg: float = 78.0
    polar_cap_blend_curve_power: float = 1.0
    polar_cap_normal_blend_start_lat_deg: float = 78.0
    polar_cap_normal_blend_end_lat_deg: float = 86.0
    polar_cap_latitude_coverage_deg: float = 60.0
    polar_cap_albedo_sharpness: float = 0.22
    polar_cap_local_contrast: float = 1.0
    polar_cap_boundary_match_strength: float = 0.35
    polar_cap_core_match_strength: float = 0.72
    terminator_band_width: float = 0.24
    terminator_softness: float = 0.085
    # Keep default terrain detail conservative; the exaggerated debug mode can override this.
    terrain_normal_strength_default: float = 0.22
    terrain_normal_strength_exaggerated: float = 0.58
    terrain_normal_z_floor: float = 0.5
    terrain_high_zoom_boost_start_px: float = 220.0
    terrain_high_zoom_boost_end_px: float = 1200.0
    terrain_high_zoom_boost_factor: float = 0.12
    # Preserve a gentle dark-side fill for crescents without making the Moon look self-illuminated.
    earthshine_base_strength: float = 0.052
    earthshine_phase_gamma: float = 1.28
    earthshine_base_multiplier: float = 0.38
    earthshine_crescent_multiplier: float = 0.88
    earthshine_floor_default: float = 0.024
    earthshine_shadow_start: float = -0.12
    earthshine_shadow_end: float = 0.46
    earthshine_max: float = 0.12
    earthshine_directional_gamma: float = 1.65
    # Fade and redden the Moon conservatively near the horizon; refraction remains future work.
    atmosphere_min_altitude_deg: float = -10.0
    atmosphere_max_altitude_deg: float = 90.0
    atmosphere_airmass_offset_deg: float = 3.0
    atmosphere_airmass_sin_floor: float = 0.115
    atmosphere_airmass_max: float = 9.0
    atmosphere_horizon_extinction_strength: float = 0.28
    atmosphere_min_extinction: float = 0.62
    atmosphere_green_loss: float = 0.10
    atmosphere_blue_loss: float = 0.24
    atmosphere_min_green: float = 0.82
    atmosphere_min_blue: float = 0.68
    # Keep the disc edge soft enough to avoid aliasing without visibly inflating the lunar limb.
    limb_softness: float = 0.018
    direct_light_gain: float = 0.92
    lighting_hemisphere_blend: float = 0.72
    lighting_hemisphere_gamma: float = 0.72
    lighting_highlight_knee: float = 0.68
    lighting_highlight_compression: float = 2.8
    albedo_brightness: float = 0.965
    albedo_contrast: float = 1.08
    albedo_saturation_retention: float = 1.035
    mare_highland_contrast_boost: float = 0.12
    tone_exposure: float = 0.84
    tone_highlight_knee: float = 0.5
    tone_highlight_compression: float = 3.0
    tone_contrast: float = 1.18
    tone_pivot: float = 0.27
    tone_saturation: float = 1.0
    tone_color_preservation: float = 0.16
    tone_gamma: float = 1.0


DEFAULT_MOON_VISUAL_SETTINGS = MoonVisualSettings()


@dataclass(frozen=True, slots=True)
class MoonDrawState:

    viewport_width_px: int
    viewport_height_px: int
    center_x_px: float
    center_y_px: float
    radius_px: float
    opacity: float
    sun_direction_moon_local: Vector3
    view_to_moon_matrix: Matrix3
    selected_lod_label: str
    earth_direction_moon_local: Vector3 = (0.0, 0.0, 1.0)
    selected_normal_lod_label: str = "none"
    terrain_normal_strength: float = DEFAULT_MOON_VISUAL_SETTINGS.terrain_normal_strength_default
    terrain_normal_strength_global: float = DEFAULT_MOON_VISUAL_SETTINGS.terrain_normal_strength_global
    terrain_normal_strength_terminator: float = DEFAULT_MOON_VISUAL_SETTINGS.terrain_normal_strength_terminator
    tiled_normal_strength: float = DEFAULT_MOON_VISUAL_SETTINGS.tiled_normal_strength
    tiled_terminator_normal_strength: float = DEFAULT_MOON_VISUAL_SETTINGS.tiled_terminator_normal_strength
    tiled_normal_max_slope: float = DEFAULT_MOON_VISUAL_SETTINGS.tiled_normal_max_slope
    polar_cap_normal_strength: float = DEFAULT_MOON_VISUAL_SETTINGS.polar_cap_normal_strength
    polar_cap_blend_start_lat_deg: float = DEFAULT_MOON_VISUAL_SETTINGS.polar_cap_blend_start_lat_deg
    polar_cap_blend_end_lat_deg: float = DEFAULT_MOON_VISUAL_SETTINGS.polar_cap_blend_end_lat_deg
    polar_cap_blend_curve_power: float = DEFAULT_MOON_VISUAL_SETTINGS.polar_cap_blend_curve_power
    polar_cap_normal_blend_start_lat_deg: float = DEFAULT_MOON_VISUAL_SETTINGS.polar_cap_normal_blend_start_lat_deg
    polar_cap_normal_blend_end_lat_deg: float = DEFAULT_MOON_VISUAL_SETTINGS.polar_cap_normal_blend_end_lat_deg
    polar_cap_albedo_sharpness: float = DEFAULT_MOON_VISUAL_SETTINGS.polar_cap_albedo_sharpness
    polar_cap_local_contrast: float = DEFAULT_MOON_VISUAL_SETTINGS.polar_cap_local_contrast
    polar_cap_boundary_match_strength: float = DEFAULT_MOON_VISUAL_SETTINGS.polar_cap_boundary_match_strength
    polar_cap_core_match_strength: float = DEFAULT_MOON_VISUAL_SETTINGS.polar_cap_core_match_strength
    tiled_normal_fade_factor: float = 0.0
    terminator_normal_factor: float = DEFAULT_MOON_VISUAL_SETTINGS.terminator_normal_min_factor
    terminator_band_width: float = DEFAULT_MOON_VISUAL_SETTINGS.terminator_band_width
    terminator_softness: float = DEFAULT_MOON_VISUAL_SETTINGS.terminator_softness
    earthshine_strength: float = DEFAULT_MOON_VISUAL_SETTINGS.earthshine_base_strength
    earthshine_floor: float = DEFAULT_MOON_VISUAL_SETTINGS.earthshine_floor_default
    atmosphere_extinction: float = 1.0
    atmosphere_tint_rgb: Vector3 = (1.0, 1.0, 1.0)
    debug_render_mode: str = "normal"
    disable_tone_mapping: bool = False
    force_tiled_texture_only: bool = False

    @property
    def diameter_px(self) -> float:

        return self.radius_px * 2.0


@dataclass(frozen=True, slots=True)
class MoonAtmosphereState:

    extinction: float
    tint_rgb: Vector3


class MoonAtmosphereModel:

    @staticmethod
    def atmosphere_for_altitude(
        altitude_deg: float,
        *,
        disabled: bool = False,
        settings: MoonVisualSettings = DEFAULT_MOON_VISUAL_SETTINGS,
    ) -> MoonAtmosphereState:

        if disabled:
            return MoonAtmosphereState(extinction=1.0, tint_rgb=(1.0, 1.0, 1.0))
        altitude = max(settings.atmosphere_min_altitude_deg, min(settings.atmosphere_max_altitude_deg, float(altitude_deg)))
        altitude_rad = math.radians(max(0.0, altitude) + settings.atmosphere_airmass_offset_deg)
        airmass = min(settings.atmosphere_airmass_max, 1.0 / max(settings.atmosphere_airmass_sin_floor, math.sin(altitude_rad)))
        horizon_factor = max(0.0, min(1.0, (airmass - 1.0) / max(1.0e-9, settings.atmosphere_airmass_max - 1.0)))
        extinction = max(settings.atmosphere_min_extinction, min(1.0, 1.0 - settings.atmosphere_horizon_extinction_strength * horizon_factor))
        tint_rgb = (
            1.0,
            max(settings.atmosphere_min_green, 1.0 - settings.atmosphere_green_loss * horizon_factor),
            max(settings.atmosphere_min_blue, 1.0 - settings.atmosphere_blue_loss * horizon_factor),
        )
        return MoonAtmosphereState(extinction=extinction, tint_rgb=tint_rgb)


class MoonEarthshineModel:

    @staticmethod
    def earthshine_strength_for_phase(
        illuminated_fraction: float,
        *,
        base_strength: float | None = None,
        disabled: bool = False,
        settings: MoonVisualSettings = DEFAULT_MOON_VISUAL_SETTINGS,
    ) -> float:

        if disabled:
            return 0.0
        lit_fraction = max(0.0, min(1.0, float(illuminated_fraction)))
        resolved_base_strength = settings.earthshine_base_strength if base_strength is None else float(base_strength)
        crescent_boost = (1.0 - lit_fraction) ** settings.earthshine_phase_gamma
        return max(
            0.0,
            min(
                settings.earthshine_max,
                resolved_base_strength
                * (settings.earthshine_base_multiplier + settings.earthshine_crescent_multiplier * crescent_boost),
            ),
        )


class MoonTiledTextureManager:

    SUPPORTED_SUFFIXES = {".png", ".jpg", ".jpeg", ".tif", ".tiff", ".webp"}
    MANIFEST_FILENAME = "build_manifest.json"
    REGISTRATION_WARNING = "Moon tiles found but not registered to global Moon texture convention."
    COVERAGE_WARNING = "Moon tiles found but bound tiles do not safely cover the visible lunar UV footprint."
    LIVE_EQUIVALENCE_WARNING = "Moon tiles found but live global/tiled sample probes do not match."
    DEFAULT_ALBEDO_TILE_ROOT = Path("assets") / "moon_tiles" / "albedo"
    DEFAULT_NORMAL_TILE_ROOT = Path("assets") / "moon_tiles" / "normal"
    DEFAULT_POLAR_CAP_ROOT = Path("assets") / "moon_tiles" / "polar_caps"

    def __init__(
        self,
        project_root: Path | None = None,
        *,
        executor: ThreadPoolExecutor | None = None,
        albedo_tile_root: Path | None = None,
        normal_tile_root: Path | None = None,
        settings: MoonTileSettings | None = None,
        active_global_albedo_source: Path | None = None,
    ) -> None:

        resolved_root = Path(project_root) if project_root is not None else Path(__file__).resolve().parents[2]
        self.project_root = resolved_root
        self.albedo_tile_root = Path(albedo_tile_root) if albedo_tile_root is not None else resolved_root / self.DEFAULT_ALBEDO_TILE_ROOT
        self.normal_tile_root = Path(normal_tile_root) if normal_tile_root is not None else resolved_root / self.DEFAULT_NORMAL_TILE_ROOT
        self.polar_cap_root = self.albedo_tile_root.parent / "polar_caps"
        self.settings = settings or MoonTileSettings()
        self.active_global_albedo_source = Path(active_global_albedo_source) if active_global_albedo_source is not None else None
        self.manifest_path = self.albedo_tile_root.parent / self.MANIFEST_FILENAME
        self._executor = executor or ThreadPoolExecutor(max_workers=1, thread_name_prefix="moon-tiles")
        self._owns_executor = executor is None
        self._tile_index_built = False
        self._available_tiles: dict[MoonTileKey, Path] = {}
        self._resident_tiles: OrderedDict[MoonTileKey, MoonTextureTile] = OrderedDict()
        self._tile_futures: dict[MoonTileKey, Future[MoonTextureTile | None]] = {}
        self._tile_cache_memory_estimate = 0
        self._tiled_mode_active = False
        self._last_requested_albedo_keys: tuple[MoonTileKey, ...] = ()
        self._last_shader_albedo_keys: tuple[MoonTileKey, ...] = ()
        self._active_shader_bindings: tuple[MoonTileShaderBinding, ...] = ()
        self._active_polar_cap_binding: MoonPolarCapBinding | None = None
        self._registration_evaluated = False
        self._tile_registration_status = "not_evaluated"
        self._tile_registered_to_global = False
        self._global_source_id = ""
        self._tile_source_id = ""
        self._detected_longitude_shift_degrees: float | None = None
        self._registration_flip_u = False
        self._registration_flip_v = False
        self._tiles_rejected_due_to_registration_mismatch = False
        self._registration_warning = ""
        self._manifest_hash = ""
        self._tile_convention_hash = ""
        self._tile_cache_generation_id = ""
        self._generation_sequence = 0
        self._generation_signature: tuple[object, ...] | None = None
        self._generation_refresh_future: Future[_MoonTileGenerationSnapshot] | None = None
        self._generation_refresh_error = ""
        self._last_generation_refresh_check = 0.0
        self._cache_invalidated_this_frame = False
        self._latest_manifest: dict[str, object] = {}
        self._polar_caps_manifest: dict[str, object] = {}
        self._live_global_source_image: QImage | None = None
        self._normal_statistics_cache: dict[tuple[object, ...], dict[str, object]] = {}
        self._live_equivalence_cache: dict[tuple[object, ...], dict[str, object]] = {}
        self._live_equivalence_futures: dict[tuple[object, ...], Future[dict[str, object]]] = {}
        self._normal_diagnostics_cache: dict[tuple[object, ...], dict[str, object]] = {}
        self._normal_diagnostics_futures: dict[tuple[object, ...], Future[dict[str, object]]] = {}
        self._polar_cap_binding_cache: dict[bool, MoonPolarCapBinding | None] = {}
        self._polar_cap_binding_futures: dict[bool, Future[MoonPolarCapBinding | None]] = {}
        self.last_diagnostics = MoonTileDiagnostics.inactive()

    def shutdown(self) -> None:

        if self._owns_executor:
            self._executor.shutdown(wait=False, cancel_futures=True)

    @property
    def tile_cache_memory_estimate(self) -> int:

        return self._tile_cache_memory_estimate

    def set_allow_unregistered_tiles_debug(self, enabled: bool) -> None:

        self.settings = replace(self.settings, allow_unregistered_tiles_debug=bool(enabled))
        self._registration_evaluated = False
        self._generation_signature = None

    @property
    def tile_cache_generation_id(self) -> str:

        return self._tile_cache_generation_id

    @staticmethod
    def _sha256_bytes(value: bytes) -> str:

        return hashlib.sha256(value).hexdigest()

    @staticmethod
    def _sha256_file(path: Path | None) -> str:

        if path is None or not path.is_file():
            return ""
        digest = hashlib.sha256()
        with path.open("rb") as handle:
            for chunk in iter(lambda: handle.read(1024 * 1024), b""):
                digest.update(chunk)
        return digest.hexdigest()

    @staticmethod
    def _path_stat_identity(path: Path | None) -> tuple[str, int, int]:

        if path is None:
            return ("", -1, -1)
        try:
            stat = path.stat()
        except OSError:
            return (str(path), -1, -1)
        return (str(path), int(stat.st_mtime_ns), int(stat.st_size))

    @classmethod
    def _tree_mtime_identity(cls, root: Path) -> tuple[str, int, tuple[tuple[str, int], ...]]:

        root_identity = cls._path_stat_identity(root)
        if not root.is_dir():
            return (str(root), root_identity[1], ())
        children: list[tuple[str, int]] = []
        try:
            for child in sorted(path for path in root.iterdir() if path.is_dir()):
                children.append((child.name, cls._path_stat_identity(child)[1]))
        except OSError:
            pass
        return (str(root), root_identity[1], tuple(children))

    def _registration_state_from_manifest(
        self,
        manifest: Mapping[str, object],
        global_source_id: str,
    ) -> dict[str, object]:

        registration_value = manifest.get("registration")
        albedo_value = manifest.get("albedo")
        convention_value = manifest.get("canonical_convention")
        transform_value = manifest.get("source_transform")
        registration = registration_value if isinstance(registration_value, dict) else {}
        albedo_manifest = albedo_value if isinstance(albedo_value, dict) else {}
        convention_manifest = convention_value if isinstance(convention_value, dict) else {}
        transform_manifest = transform_value if isinstance(transform_value, dict) else {}
        tile_source_id = str(albedo_manifest.get("source_sha256") or "")
        convention_bytes = json.dumps(
            {"canonical_convention": convention_manifest, "source_transform": transform_manifest},
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
        tile_convention_hash = self._sha256_bytes(convention_bytes)
        detected_longitude_shift_degrees = (
            float(registration["detected_longitude_shift_degrees"])
            if registration.get("detected_longitude_shift_degrees") is not None
            else None
        )
        registration_flip_u = bool(registration.get("possible_u_flip_detected", False))
        registration_flip_v = bool(registration.get("possible_v_flip_detected", False))
        declared_global_id = str(registration.get("global_source_sha256") or "")
        certificate_passes = bool(registration.get("registered_to_active_global_texture", False))
        active_source_matches = bool(global_source_id and declared_global_id == global_source_id)
        if not bool(self.settings.enforce_source_registration):
            tile_registration_status = "registration_not_enforced"
            tile_registered_to_global = certificate_passes and active_source_matches
            tiles_rejected_due_to_registration_mismatch = False
        elif certificate_passes and active_source_matches:
            tile_registration_status = "registered"
            tile_registered_to_global = True
            tiles_rejected_due_to_registration_mismatch = False
        elif bool(self.settings.allow_unregistered_tiles_debug):
            tile_registration_status = "debug_override_unregistered"
            tile_registered_to_global = False
            tiles_rejected_due_to_registration_mismatch = False
        else:
            tile_registration_status = "unregistered_rejected"
            tile_registered_to_global = False
            tiles_rejected_due_to_registration_mismatch = True
        registration_warning = self.REGISTRATION_WARNING if tiles_rejected_due_to_registration_mismatch else ""
        return {
            "global_source_id": global_source_id,
            "tile_source_id": tile_source_id,
            "tile_convention_hash": tile_convention_hash,
            "detected_longitude_shift_degrees": detected_longitude_shift_degrees,
            "registration_flip_u": registration_flip_u,
            "registration_flip_v": registration_flip_v,
            "tile_registration_status": tile_registration_status,
            "tile_registered_to_global": tile_registered_to_global,
            "tiles_rejected_due_to_registration_mismatch": tiles_rejected_due_to_registration_mismatch,
            "registration_warning": registration_warning,
        }

    def _build_generation_snapshot(self) -> _MoonTileGenerationSnapshot:

        try:
            manifest_bytes = self.manifest_path.read_bytes() if self.manifest_path.is_file() else b""
        except OSError:
            manifest_bytes = b""
        manifest_hash = self._sha256_bytes(manifest_bytes) if manifest_bytes else ""
        manifest: dict[str, object] = {}
        if manifest_bytes:
            try:
                parsed_manifest = json.loads(manifest_bytes.decode("utf-8"))
            except (UnicodeDecodeError, ValueError, TypeError):
                parsed_manifest = {}
            manifest = parsed_manifest if isinstance(parsed_manifest, dict) else {}
        available_tiles: dict[MoonTileKey, Path] = {}
        available_tiles.update(self._scan_tile_root(self.albedo_tile_root, "albedo"))
        available_tiles.update(self._scan_tile_root(self.normal_tile_root, "normal"))
        global_source_id = self._sha256_file(self.active_global_albedo_source)
        registration_state = self._registration_state_from_manifest(manifest, global_source_id)
        signature = (
            manifest_hash,
            self._tree_mtime_identity(self.albedo_tile_root),
            self._tree_mtime_identity(self.normal_tile_root),
            self._tree_mtime_identity(self.polar_cap_root),
            self._path_stat_identity(self.active_global_albedo_source),
            bool(self.settings.enforce_source_registration),
            bool(self.settings.allow_unregistered_tiles_debug),
        )
        return _MoonTileGenerationSnapshot(
            signature=signature,
            manifest=manifest,
            manifest_hash=manifest_hash,
            available_tiles=available_tiles,
            global_source_id=str(registration_state["global_source_id"]),
            tile_source_id=str(registration_state["tile_source_id"]),
            tile_convention_hash=str(registration_state["tile_convention_hash"]),
            detected_longitude_shift_degrees=registration_state["detected_longitude_shift_degrees"],
            registration_flip_u=bool(registration_state["registration_flip_u"]),
            registration_flip_v=bool(registration_state["registration_flip_v"]),
            tile_registration_status=str(registration_state["tile_registration_status"]),
            tile_registered_to_global=bool(registration_state["tile_registered_to_global"]),
            tiles_rejected_due_to_registration_mismatch=bool(registration_state["tiles_rejected_due_to_registration_mismatch"]),
            registration_warning=str(registration_state["registration_warning"]),
        )

    def _clear_generation_dependent_cache(self) -> None:

        for future in self._tile_futures.values():
            future.cancel()
        self._tile_futures.clear()
        self._resident_tiles.clear()
        self._tile_cache_memory_estimate = 0
        self._active_shader_bindings = ()
        self._active_polar_cap_binding = None
        self._last_requested_albedo_keys = ()
        self._last_shader_albedo_keys = ()
        self._live_global_source_image = None
        self._normal_statistics_cache.clear()
        self._live_equivalence_cache.clear()
        self._normal_diagnostics_cache.clear()
        for future in self._live_equivalence_futures.values():
            future.cancel()
        self._live_equivalence_futures.clear()
        for future in self._normal_diagnostics_futures.values():
            future.cancel()
        self._normal_diagnostics_futures.clear()
        for future in self._polar_cap_binding_futures.values():
            future.cancel()
        self._polar_cap_binding_futures.clear()
        self._polar_cap_binding_cache.clear()

    def _apply_generation_snapshot(self, snapshot: _MoonTileGenerationSnapshot) -> None:

        if snapshot.signature == self._generation_signature:
            self._cache_invalidated_this_frame = False
            return
        had_generation = self._generation_signature is not None
        self._generation_signature = snapshot.signature
        self._manifest_hash = snapshot.manifest_hash
        self._generation_sequence += 1
        self._tile_cache_generation_id = f"{self._generation_sequence}:{snapshot.manifest_hash[:12] or 'no-manifest'}"
        self._cache_invalidated_this_frame = had_generation
        self._latest_manifest = dict(snapshot.manifest)
        caps_manifest = snapshot.manifest.get("polar_caps") if isinstance(snapshot.manifest, Mapping) else None
        self._polar_caps_manifest = dict(caps_manifest) if isinstance(caps_manifest, dict) else {}
        self._available_tiles = dict(snapshot.available_tiles)
        self._tile_index_built = True
        self._registration_evaluated = True
        self._global_source_id = snapshot.global_source_id
        self._tile_source_id = snapshot.tile_source_id
        self._tile_convention_hash = snapshot.tile_convention_hash
        self._detected_longitude_shift_degrees = snapshot.detected_longitude_shift_degrees
        self._registration_flip_u = snapshot.registration_flip_u
        self._registration_flip_v = snapshot.registration_flip_v
        self._tile_registration_status = snapshot.tile_registration_status
        self._tile_registered_to_global = snapshot.tile_registered_to_global
        self._tiles_rejected_due_to_registration_mismatch = snapshot.tiles_rejected_due_to_registration_mismatch
        self._registration_warning = snapshot.registration_warning
        self._clear_generation_dependent_cache()

    def _poll_generation_refresh(self) -> None:

        future = self._generation_refresh_future
        if future is None or not future.done():
            return
        self._generation_refresh_future = None
        try:
            snapshot = future.result()
        except Exception as exc:
            self._generation_refresh_error = f"{type(exc).__name__}: {exc}"
            self._cache_invalidated_this_frame = False
            return
        self._generation_refresh_error = ""
        self._apply_generation_snapshot(snapshot)

    def _request_generation_refresh(self) -> None:

        self._poll_generation_refresh()
        if self._generation_refresh_future is not None:
            return
        now = perf_counter()
        has_generation = self._generation_signature is not None
        live_watch_due = bool(self.settings.live_asset_watch_enabled) and (
            now - self._last_generation_refresh_check
        ) >= max(0.0, float(self.settings.live_asset_watch_interval_seconds))
        if has_generation and not live_watch_due:
            self._cache_invalidated_this_frame = False
            return
        self._last_generation_refresh_check = now
        self._generation_refresh_future = self._executor.submit(self._build_generation_snapshot)
        self._poll_generation_refresh()

    def _refresh_cache_generation(self) -> None:

        self._apply_generation_snapshot(self._build_generation_snapshot())

    def _ensure_registration_status(self) -> None:

        if self._registration_evaluated:
            return
        self._refresh_cache_generation()

    def _with_registration_state(self, diagnostics: MoonTileDiagnostics) -> MoonTileDiagnostics:

        return replace(
            diagnostics,
            global_source_id=self._global_source_id,
            tile_source_id=self._tile_source_id,
            tile_registration_status=self._tile_registration_status,
            tile_registered_to_global=self._tile_registered_to_global,
            detected_longitude_shift_degrees=self._detected_longitude_shift_degrees,
            flip_u=self._registration_flip_u,
            flip_v=self._registration_flip_v,
            tiles_rejected_due_to_registration_mismatch=self._tiles_rejected_due_to_registration_mismatch,
            registration_warning=self._registration_warning,
            tile_root_path=str(self.albedo_tile_root.parent),
            manifest_hash=self._manifest_hash,
            tile_convention_hash=self._tile_convention_hash,
            tile_cache_generation_id=self._tile_cache_generation_id,
            cache_invalidated_this_frame=self._cache_invalidated_this_frame,
            active_source_mode="fallback" if self._tiles_rejected_due_to_registration_mismatch else diagnostics.active_source_mode,
        )

    @staticmethod
    def _image_rgb_at_uv(image: QImage, texture_u: float, texture_v: float) -> tuple[int, int, int]:

        width = max(1, int(image.width()))
        height = max(1, int(image.height()))
        x_pos = min(width - 1, max(0, int((float(texture_u) % 1.0) * width)))
        y_pos = min(height - 1, max(0, int(max(0.0, min(1.0, float(texture_v))) * height)))
        color = image.pixelColor(x_pos, y_pos)
        return int(color.red()), int(color.green()), int(color.blue())

    @staticmethod
    def _rgb_delta(lhs: tuple[int, int, int], rhs: tuple[int, int, int]) -> float:

        return max(abs(lhs[index] - rhs[index]) for index in range(3)) / 255.0

    def _visible_uv_samples(self, draw_state: MoonDrawState) -> tuple[tuple[float, float], ...]:

        grid_size = max(9, int(self.settings.visible_coverage_grid_samples))
        if grid_size % 2 == 0:
            grid_size += 1
        coordinates = tuple(-1.0 + 2.0 * index / max(1, grid_size - 1) for index in range(grid_size))
        disc_points = [(x_pos, y_pos) for y_pos in coordinates for x_pos in coordinates if x_pos * x_pos + y_pos * y_pos <= 1.0]
        disc_points.extend(
            (0.999 * math.cos(2.0 * math.pi * index / 64.0), 0.999 * math.sin(2.0 * math.pi * index / 64.0))
            for index in range(64)
        )
        samples: list[tuple[float, float]] = []
        for sample_x, sample_y in disc_points:
            normal_view = (float(sample_x), float(sample_y), math.sqrt(max(0.0, 1.0 - sample_x * sample_x - sample_y * sample_y)))
            normal_moon = self._normalize_vector(
                (
                    self._dot(draw_state.view_to_moon_matrix[0], normal_view),
                    self._dot(draw_state.view_to_moon_matrix[1], normal_view),
                    self._dot(draw_state.view_to_moon_matrix[2], normal_view),
                )
            )
            samples.append(self.moon_normal_to_uv(normal_moon))
        samples.extend(
            (float(pole["u"]), float(pole["v"]))
            for pole in self._visible_pole_reports(draw_state)
        )
        return tuple(samples)

    def _visible_pole_reports(self, draw_state: MoonDrawState) -> tuple[dict[str, object], ...]:

        reports: list[dict[str, object]] = []
        for label, normal_moon in (("north_pole", (0.0, 1.0, 0.0)), ("south_pole", (0.0, -1.0, 0.0))):
            normal_view = (
                draw_state.view_to_moon_matrix[0][0] * normal_moon[0] + draw_state.view_to_moon_matrix[1][0] * normal_moon[1] + draw_state.view_to_moon_matrix[2][0] * normal_moon[2],
                draw_state.view_to_moon_matrix[0][1] * normal_moon[0] + draw_state.view_to_moon_matrix[1][1] * normal_moon[1] + draw_state.view_to_moon_matrix[2][1] * normal_moon[2],
                draw_state.view_to_moon_matrix[0][2] * normal_moon[0] + draw_state.view_to_moon_matrix[1][2] * normal_moon[1] + draw_state.view_to_moon_matrix[2][2] * normal_moon[2],
            )
            if normal_view[2] < -1.0e-8:
                continue
            texture_u, texture_v = self.moon_normal_to_uv(normal_moon)
            reports.append(
                {
                    "label": label,
                    "u": texture_u,
                    "v": texture_v,
                    "latitude_deg": 90.0 if label == "north_pole" else -90.0,
                    "longitude_deg": 0.0,
                    "disc_x": float(normal_view[0]),
                    "disc_y": float(normal_view[1]),
                }
            )
        return tuple(reports)

    def _coverage_report(
        self,
        draw_state: MoonDrawState,
        bindings: Sequence[MoonTileShaderBinding],
    ) -> dict[str, object]:

        samples = self._visible_uv_samples(draw_state)
        routes = tuple(MoonRendererGL.tile_sample_route_for_uv(texture_u, texture_v, bindings) for texture_u, texture_v in samples)
        tiled_count = sum(1 for route in routes if route.source == "tile")
        coverage = tiled_count / max(1, len(routes))
        u_values = [sample[0] for sample in samples]
        v_values = [sample[1] for sample in samples]
        pole_proximities = tuple(MoonRendererGL.pole_proximity_factor(texture_v) for _texture_u, texture_v in samples)
        seam_proximities = tuple(MoonRendererGL.seam_proximity_factor(texture_u) for texture_u, _texture_v in samples)
        visible_poles = self._visible_pole_reports(draw_state)
        nearest_pole = max(
            visible_poles,
            key=lambda item: MoonRendererGL.pole_proximity_factor(float(item["v"])),
            default=None,
        )
        near_north = any(
            MoonRendererGL.polar_albedo_fade_factor_for_uv(texture_v) > 0.0 and texture_v <= 0.5
            for _texture_u, texture_v in samples
        )
        near_south = any(
            MoonRendererGL.polar_albedo_fade_factor_for_uv(texture_v) > 0.0 and texture_v >= 0.5
            for _texture_u, texture_v in samples
        )
        crosses_seam = bool(u_values and max(u_values) - min(u_values) > 0.5)
        max_pole_proximity = max(pole_proximities, default=0.0)
        max_seam_proximity = max(seam_proximities, default=0.0)
        footprint = (
            f"samples={len(samples)} u={min(u_values, default=0.0):.4f}..{max(u_values, default=0.0):.4f} "
            f"v={min(v_values, default=0.0):.4f}..{max(v_values, default=0.0):.4f}"
        )
        bounds = tuple(
            f"{self._format_tile_key(binding.key)}:"
            f"[{binding.uv_bounds.u_min:.4f},{binding.uv_bounds.v_min:.4f}].."
            f"[{binding.uv_bounds.u_max:.4f},{binding.uv_bounds.v_max:.4f}]"
            for binding in bindings
        )
        return {
            "coverage": float(coverage),
            "fallback": float(max(0.0, 1.0 - coverage)),
            "sample_count": int(len(samples)),
            "footprint": footprint,
            "bounds": bounds,
            "artifact_latitude_deg": None if nearest_pole is None else float(nearest_pole["latitude_deg"]),
            "artifact_longitude_deg": None if nearest_pole is None else float(nearest_pole["longitude_deg"]),
            "artifact_disc_position": "" if nearest_pole is None else (
                f"{nearest_pole['label']} disc=({float(nearest_pole['disc_x']):.4f},{float(nearest_pole['disc_y']):.4f})"
            ),
            "near_north_pole": near_north,
            "near_south_pole": near_south,
            "crosses_seam": crosses_seam,
            "pole_proximity": max_pole_proximity,
            "seam_proximity": max_seam_proximity,
            "polar_normal_fade_factor": max(
                (MoonRendererGL.polar_normal_fade_factor_for_uv(texture_v) for _texture_u, texture_v in samples),
                default=0.0,
            ),
            "polar_albedo_fade_factor": max(
                (MoonRendererGL.polar_albedo_fade_factor_for_uv(texture_v) for _texture_u, texture_v in samples),
                default=0.0,
            ),
            # The shader and this route evaluator only form local UVs after a contains test.
            "out_of_bounds_sampling": False,
        }

    def _with_uv_singularity_state(
        self,
        diagnostics: MoonTileDiagnostics,
        draw_state: MoonDrawState,
    ) -> MoonTileDiagnostics:

        report = self._coverage_report(draw_state, ())
        polar_normal_fade_factor = float(report["polar_normal_fade_factor"])
        polar_albedo_fade_factor = float(report["polar_albedo_fade_factor"])
        return replace(
            diagnostics,
            artifact_lunar_latitude_deg=report["artifact_latitude_deg"],
            artifact_lunar_longitude_deg=report["artifact_longitude_deg"],
            artifact_disc_position=str(report["artifact_disc_position"]),
            visible_uv_near_north_pole=bool(report["near_north_pole"]),
            visible_uv_near_south_pole=bool(report["near_south_pole"]),
            visible_uv_crosses_longitude_seam=bool(report["crosses_seam"]),
            visible_pole_proximity=float(report["pole_proximity"]),
            visible_seam_proximity=float(report["seam_proximity"]),
            polar_normal_fade_factor=polar_normal_fade_factor,
            polar_albedo_fade_factor=polar_albedo_fade_factor,
            polar_analytic_normal_active=bool(
                DEFAULT_MOON_VISUAL_SETTINGS.polar_use_analytic_normal
                and polar_normal_fade_factor >= 1.0 - 1.0e-6
            ),
            seam_wrap_active=True,
            pole_singularity_guard_active=bool(polar_normal_fade_factor > 0.0 or polar_albedo_fade_factor > 0.0),
        )

    @staticmethod
    def _normal_slope_statistics(image: QImage, *, max_slope: float) -> dict[str, object]:

        converted = image.convertToFormat(QImage.Format.Format_RGB888)
        width = max(1, int(converted.width()))
        height = max(1, int(converted.height()))
        bytes_per_line = int(converted.bytesPerLine())
        rgb = np.frombuffer(
            converted.bits(),
            dtype=np.uint8,
            count=bytes_per_line * height,
        ).reshape((height, bytes_per_line))[:, : width * 3].reshape((height, width, 3)).astype(np.float32)
        tangent = rgb[:, :, :2] / 127.5 - 1.0
        normal_z = np.maximum(rgb[:, :, 2] / 127.5 - 1.0, 1.0e-4)
        slope = np.sqrt(np.sum(tangent * tangent, axis=2)) / normal_z
        gradient_x = np.diff(slope, axis=1, append=slope[:, -1:])
        gradient_y = np.diff(slope, axis=0, append=slope[-1:, :])
        gradient = np.sqrt(gradient_x * gradient_x + gradient_y * gradient_y)
        threshold = max(0.0, float(max_slope))
        return {
            "min_slope": float(np.min(slope)),
            "max_slope": float(np.max(slope)),
            "mean_slope": float(np.mean(slope)),
            "p95_slope": float(np.percentile(slope, 95.0)),
            "max_gradient": float(np.max(gradient)),
            "mean_gradient": float(np.mean(gradient)),
            "outlier_count": int(np.count_nonzero(slope > threshold)),
            "sample_count": int(slope.size),
        }

    def _cached_normal_slope_statistics(
        self,
        image: QImage,
        cache_key: tuple[object, ...],
        *,
        max_slope: float,
    ) -> dict[str, object]:

        statistics_key = (*cache_key, "normal-slope", round(float(max_slope), 6))
        statistics = self._normal_statistics_cache.get(statistics_key)
        if statistics is None:
            statistics = self._normal_slope_statistics(image, max_slope=max_slope)
            self._normal_statistics_cache[statistics_key] = statistics
        return dict(statistics)

    def _problematic_terminator_tile_key(
        self,
        draw_state: MoonDrawState,
        bindings: Sequence[MoonTileShaderBinding],
    ) -> str:

        candidates: list[tuple[float, str]] = []
        bound_normal_keys = {
            self._format_tile_key(binding.key): self._format_tile_key(binding.normal_tile.key)
            for binding in bindings
            if binding.normal_tile is not None
        }
        for y_pos in (-0.82, -0.62, -0.42, -0.22):
            for x_pos in (-0.82, -0.62, -0.42, -0.22):
                radius_squared = x_pos * x_pos + y_pos * y_pos
                if radius_squared >= 0.98:
                    continue
                normal_view = (x_pos, y_pos, math.sqrt(max(0.0, 1.0 - radius_squared)))
                normal_moon = self._normalize_vector(
                    (
                        self._dot(draw_state.view_to_moon_matrix[0], normal_view),
                        self._dot(draw_state.view_to_moon_matrix[1], normal_view),
                        self._dot(draw_state.view_to_moon_matrix[2], normal_view),
                    )
                )
                route = MoonRendererGL.tile_sample_route_for_uv(*self.moon_normal_to_uv(normal_moon), bindings)
                if route.source != "tile":
                    continue
                distance_to_terminator = abs(self._dot(normal_moon, draw_state.sun_direction_moon_local))
                candidates.append((distance_to_terminator, bound_normal_keys.get(route.tile_key, route.tile_key)))
        return min(candidates)[1] if candidates else ""

    def _compute_normal_tile_diagnostics(
        self,
        draw_state: MoonDrawState,
        bindings: Sequence[MoonTileShaderBinding],
        global_normal_lod: MoonTextureLod | None,
    ) -> dict[str, object]:

        max_allowed_slope = max(0.0, float(draw_state.tiled_normal_max_slope))
        statistics: list[dict[str, object]] = []
        for binding in bindings:
            normal_tile = binding.normal_tile
            if normal_tile is None or normal_tile.image.isNull():
                continue
            tile_statistics = self._cached_normal_slope_statistics(
                normal_tile.image,
                normal_tile.cache_key,
                max_slope=max_allowed_slope,
            )
            tile_statistics["tile_key"] = self._format_tile_key(normal_tile.key)
            tile_statistics["effective_max_slope"] = min(float(tile_statistics["max_slope"]), max_allowed_slope)
            tile_statistics["slope_clamp_applied"] = bool(float(tile_statistics["max_slope"]) > max_allowed_slope)
            if global_normal_lod is not None and not global_normal_lod.image.isNull():
                bounds = binding.uv_bounds
                global_image = global_normal_lod.image
                left = max(0, min(global_image.width() - 1, int(math.floor(bounds.u_min * global_image.width()))))
                top = max(0, min(global_image.height() - 1, int(math.floor(bounds.v_min * global_image.height()))))
                right = max(left + 1, min(global_image.width(), int(math.ceil(bounds.u_max * global_image.width()))))
                bottom = max(top + 1, min(global_image.height(), int(math.ceil(bounds.v_max * global_image.height()))))
                comparison_image = global_image.copy(left, top, right - left, bottom - top).scaled(
                    normal_tile.image.size(),
                    Qt.AspectRatioMode.IgnoreAspectRatio,
                    Qt.TransformationMode.SmoothTransformation,
                )
                global_statistics = self._cached_normal_slope_statistics(
                    comparison_image,
                    (*global_normal_lod.cache_key, self._format_tile_key(normal_tile.key)),
                    max_slope=max_allowed_slope,
                )
                tile_statistics["global_mean_slope"] = global_statistics["mean_slope"]
                tile_statistics["global_max_slope"] = global_statistics["max_slope"]
                tile_statistics["mean_slope_delta"] = float(tile_statistics["mean_slope"]) - float(global_statistics["mean_slope"])
            statistics.append(tile_statistics)
        max_slope = max((float(item["max_slope"]) for item in statistics), default=None)
        risk = any(
            int(item["outlier_count"]) > 0
            or float(item["max_gradient"]) > max(0.08, max_allowed_slope * 0.6)
            for item in statistics
        )
        return {
            "statistics": tuple(statistics),
            "max_slope": max_slope,
            "risk": bool(risk),
            "problematic_tile_key": self._problematic_terminator_tile_key(draw_state, bindings),
        }

    @staticmethod
    def _normal_diagnostics_pending_report() -> dict[str, object]:

        return {
            "statistics": (),
            "max_slope": None,
            "risk": False,
            "problematic_tile_key": "",
        }

    def _normal_diagnostics_cache_key(
        self,
        draw_state: MoonDrawState,
        bindings: Sequence[MoonTileShaderBinding],
        global_normal_lod: MoonTextureLod | None,
    ) -> tuple[object, ...]:

        rounded_view = tuple(round(component, 6) for row in draw_state.view_to_moon_matrix for component in row)
        rounded_sun = tuple(round(component, 6) for component in draw_state.sun_direction_moon_local)
        return (
            "normal-diagnostics",
            self._tile_cache_generation_id,
            tuple(tuple(binding.normal_tile.cache_key) for binding in bindings if binding.normal_tile is not None),
            tuple(global_normal_lod.cache_key) if global_normal_lod is not None else (),
            round(float(draw_state.tiled_normal_max_slope), 6),
            rounded_view,
            rounded_sun,
        )

    def _normal_tile_diagnostics(
        self,
        draw_state: MoonDrawState,
        bindings: Sequence[MoonTileShaderBinding],
        global_normal_lod: MoonTextureLod | None,
    ) -> dict[str, object]:

        if not bindings:
            return self._normal_diagnostics_pending_report()
        cache_key = self._normal_diagnostics_cache_key(draw_state, bindings, global_normal_lod)
        cached = self._normal_diagnostics_cache.get(cache_key)
        if cached is not None:
            return dict(cached)
        future = self._normal_diagnostics_futures.get(cache_key)
        if future is not None and future.done():
            self._normal_diagnostics_futures.pop(cache_key, None)
            try:
                report = future.result()
            except Exception:
                report = self._normal_diagnostics_pending_report()
            self._normal_diagnostics_cache[cache_key] = dict(report)
            return dict(report)
        if future is None:
            self._normal_diagnostics_futures[cache_key] = self._executor.submit(
                self._compute_normal_tile_diagnostics,
                draw_state,
                tuple(bindings),
                global_normal_lod,
            )
            future = self._normal_diagnostics_futures[cache_key]
            if future.done():
                self._normal_diagnostics_futures.pop(cache_key, None)
                try:
                    report = future.result()
                except Exception:
                    report = self._normal_diagnostics_pending_report()
                self._normal_diagnostics_cache[cache_key] = dict(report)
                return dict(report)
        return self._normal_diagnostics_pending_report()

    def _compute_live_equivalence_report(
        self,
        bindings: Sequence[MoonTileShaderBinding],
        global_albedo_lod: MoonTextureLod | None = None,
    ) -> dict[str, object]:

        if not bindings:
            return {"status": "no_bound_tiles", "passed": False, "mean_delta": None, "max_delta": None, "landmarks": ()}
        if global_albedo_lod is not None and not global_albedo_lod.image.isNull():
            source_image = QImage(global_albedo_lod.image).convertToFormat(QImage.Format.Format_RGB888)
        else:
            source_path = self.active_global_albedo_source
            if source_path is None or not source_path.is_file():
                return {"status": "global_source_unavailable", "passed": False, "mean_delta": None, "max_delta": None, "landmarks": ()}
            if self._live_global_source_image is None:
                loaded_image = QImage(str(source_path)).convertToFormat(QImage.Format.Format_RGB888)
                if loaded_image.isNull():
                    return {"status": "global_source_unreadable", "passed": False, "mean_delta": None, "max_delta": None, "landmarks": ()}
                self._live_global_source_image = loaded_image
            source_image = self._live_global_source_image
        binding_by_key = {self._format_tile_key(binding.key): binding for binding in bindings}
        scaled_references: dict[tuple[int, int], QImage] = {}
        probe_points: list[tuple[str, float, float, bool]] = [
            (name, texture_u, texture_v, True) for name, texture_u, texture_v in MOON_LANDMARK_UVS
        ]
        for binding in bindings:
            bounds = binding.uv_bounds
            for suffix, u_mix, v_mix in (("center", 0.5, 0.5), ("q1", 0.25, 0.25), ("q3", 0.75, 0.75)):
                probe_points.append(
                    (
                        f"{self._format_tile_key(binding.key)}:{suffix}",
                        bounds.u_min + (bounds.u_max - bounds.u_min) * u_mix,
                        bounds.v_min + (bounds.v_max - bounds.v_min) * v_mix,
                        False,
                    )
                )
        deltas: list[float] = []
        landmark_reports: list[str] = []
        for label, texture_u, texture_v, is_landmark in probe_points:
            route = MoonRendererGL.tile_sample_route_for_uv(texture_u, texture_v, bindings, tile_edge_fade_fraction=0.0)
            if route.source != "tile":
                if is_landmark:
                    landmark_reports.append(f"{label}:global_fallback")
                continue
            binding = binding_by_key.get(route.tile_key)
            if binding is None:
                if is_landmark:
                    landmark_reports.append(f"{label}:route_error")
                return {"status": "route_error", "passed": False, "mean_delta": None, "max_delta": None, "landmarks": tuple(landmark_reports)}
            columns, rows = self.tile_grid_dimensions(binding.key.lod_level)
            reference_dimensions = (columns * int(binding.albedo_tile.image.width()), rows * int(binding.albedo_tile.image.height()))
            reference = scaled_references.get(reference_dimensions)
            if reference is None:
                reference = source_image.scaled(
                    reference_dimensions[0],
                    reference_dimensions[1],
                    Qt.AspectRatioMode.IgnoreAspectRatio,
                    Qt.TransformationMode.SmoothTransformation,
                )
                scaled_references[reference_dimensions] = reference
            point_delta = self._rgb_delta(
                self._image_rgb_at_uv(reference, texture_u, texture_v),
                self._image_rgb_at_uv(binding.albedo_tile.image, route.local_u, route.local_v),
            )
            deltas.append(point_delta)
            if is_landmark:
                landmark_reports.append(f"{label}:{point_delta:.6f}")
        if not deltas:
            return {"status": "no_tiled_probe_samples", "passed": False, "mean_delta": None, "max_delta": None, "landmarks": tuple(landmark_reports)}
        mean_delta = sum(deltas) / len(deltas)
        max_delta = max(deltas)
        passed = max_delta <= max(0.0, float(self.settings.live_probe_max_color_delta))
        return {
            "status": "pass" if passed else "fail",
            "passed": passed,
            "mean_delta": float(mean_delta),
            "max_delta": float(max_delta),
            "landmarks": tuple(landmark_reports),
        }

    @staticmethod
    def _live_equivalence_pending_report() -> dict[str, object]:

        return {"status": "pending", "passed": False, "mean_delta": None, "max_delta": None, "landmarks": ()}

    def _live_equivalence_cache_key(
        self,
        bindings: Sequence[MoonTileShaderBinding],
        global_albedo_lod: MoonTextureLod | None,
    ) -> tuple[object, ...]:

        return (
            "live-equivalence",
            self._tile_cache_generation_id,
            self._manifest_hash,
            self._tile_convention_hash,
            tuple(tuple(binding.albedo_tile.cache_key) for binding in bindings),
            tuple(global_albedo_lod.cache_key) if global_albedo_lod is not None else ("source", self._global_source_id),
        )

    def _live_equivalence_report(
        self,
        bindings: Sequence[MoonTileShaderBinding],
        global_albedo_lod: MoonTextureLod | None = None,
    ) -> dict[str, object]:

        if not bindings:
            return {"status": "no_bound_tiles", "passed": False, "mean_delta": None, "max_delta": None, "landmarks": ()}
        cache_key = self._live_equivalence_cache_key(bindings, global_albedo_lod)
        cached = self._live_equivalence_cache.get(cache_key)
        if cached is not None:
            return dict(cached)
        future = self._live_equivalence_futures.get(cache_key)
        if future is not None and future.done():
            self._live_equivalence_futures.pop(cache_key, None)
            try:
                report = future.result()
            except Exception:
                report = {"status": "error", "passed": False, "mean_delta": None, "max_delta": None, "landmarks": ()}
            self._live_equivalence_cache[cache_key] = dict(report)
            return dict(report)
        if future is None:
            self._live_equivalence_futures[cache_key] = self._executor.submit(
                self._compute_live_equivalence_report,
                tuple(bindings),
                global_albedo_lod,
            )
            future = self._live_equivalence_futures[cache_key]
            if future.done():
                self._live_equivalence_futures.pop(cache_key, None)
                try:
                    report = future.result()
                except Exception:
                    report = {"status": "error", "passed": False, "mean_delta": None, "max_delta": None, "landmarks": ()}
                self._live_equivalence_cache[cache_key] = dict(report)
                return dict(report)
        return self._live_equivalence_pending_report()

    def prepare_tiles(
        self,
        draw_state: MoonDrawState,
        moon_state: MoonState,
        *,
        normal_maps_active: bool = False,
        global_albedo_lod: MoonTextureLod | None = None,
        global_normal_lod: MoonTextureLod | None = None,
    ) -> MoonTileDiagnostics:

        benchmark_recorder = get_benchmark_recorder() if BENCHMARK_ENABLED else None

        benchmark_token = benchmark_recorder.start_section("moon.prepare_tiles") if benchmark_recorder is not None else None

        def stop_benchmark(diagnostics: MoonTileDiagnostics) -> None:

            if benchmark_recorder is not None:

                benchmark_recorder.stop_section(

                    benchmark_token,

                    metadata={

                        "tiled_mode_active": diagnostics.tiled_mode_active,

                        "requested_tiles": diagnostics.requested_tile_count,

                        "resident_tiles": diagnostics.resident_tile_count,

                        "pending_tiles": diagnostics.pending_tile_count,

                        "missing_tiles": diagnostics.missing_tile_count,

                        "available_tiles": diagnostics.available_tile_count,

                        "visible_tiles": diagnostics.visible_tile_count,

                        "fallback_reason": diagnostics.fallback_reason,

                    },

                )

        deadline = float("inf")
        self._request_generation_refresh()
        tile_poll_limit = self._render_tile_future_poll_limit()
        self._poll_tile_futures(max_results=tile_poll_limit)
        self._active_shader_bindings = ()
        if self._generation_signature is None:
            self._tiled_mode_active = False
            self._last_requested_albedo_keys = ()
            self._last_shader_albedo_keys = ()
            diagnostics = self._with_uv_singularity_state(
                self._with_registration_state(MoonTileDiagnostics.inactive("moon_tile_index_pending")),
                draw_state,
            )
            self.last_diagnostics = diagnostics
            stop_benchmark(diagnostics)
            return diagnostics
        available_albedo_keys = tuple(key for key in self._available_tiles if key.kind == "albedo")
        if not available_albedo_keys:
            self._tiled_mode_active = False
            self._last_requested_albedo_keys = ()
            self._last_shader_albedo_keys = ()
            diagnostics = self._with_uv_singularity_state(
                self._with_registration_state(MoonTileDiagnostics.inactive("moon_tile_directory_unavailable")),
                draw_state,
            )
            self.last_diagnostics = diagnostics
            stop_benchmark(diagnostics)
            return diagnostics
        if self._tiles_rejected_due_to_registration_mismatch:
            self._tiled_mode_active = False
            self._last_requested_albedo_keys = ()
            self._last_shader_albedo_keys = ()
            diagnostics = self._with_uv_singularity_state(self._with_registration_state(
                replace(
                    MoonTileDiagnostics.inactive("unregistered_tiles_global_fallback"),
                    available_tile_count=len(available_albedo_keys),
                )
            ), draw_state)
            self.last_diagnostics = diagnostics
            stop_benchmark(diagnostics)
            return diagnostics

        def budget_exceeded() -> bool:

            return perf_counter() >= deadline

        def budget_fallback(reason: str, *, target_level: int = -1) -> MoonTileDiagnostics:

            pending_count = sum(1 for future in self._tile_futures.values() if not future.done())
            diagnostics = self._with_uv_singularity_state(self._with_registration_state(
                replace(
                    MoonTileDiagnostics.inactive(reason),
                    tile_lod_level=target_level,
                    available_tile_count=len(available_albedo_keys),
                    tile_cache_memory_estimate=self._tile_cache_memory_estimate,
                    pending_tile_count=pending_count,
                )
            ), draw_state)
            self.last_diagnostics = diagnostics
            return diagnostics
        if self.settings.disable_tiled_albedo or self.settings.force_global_moon_texture:
            self._tiled_mode_active = False
            self._last_requested_albedo_keys = ()
            self._last_shader_albedo_keys = ()
            fallback_reason = "force_global_moon_texture" if self.settings.force_global_moon_texture else "disable_tiled_albedo"
            diagnostics = self._with_uv_singularity_state(self._with_registration_state(
                replace(
                    MoonTileDiagnostics.inactive(fallback_reason),
                    available_tile_count=len(available_albedo_keys),
                    activation_warning="Moon tiled albedo disabled; using global Moon texture.",
                )
            ), draw_state)
            self.last_diagnostics = diagnostics
            stop_benchmark(diagnostics)
            return diagnostics

        diameter_px = float(draw_state.diameter_px)
        activate_threshold = max(1.0, float(self.settings.activation_diameter_px))
        deactivate_threshold = max(1.0, activate_threshold - max(0.0, float(self.settings.hysteresis_px)))
        if self._tiled_mode_active:
            self._tiled_mode_active = diameter_px >= deactivate_threshold
        else:
            self._tiled_mode_active = diameter_px >= activate_threshold
        if not self._tiled_mode_active:
            self._last_requested_albedo_keys = ()
            self._last_shader_albedo_keys = ()
            diagnostics = self._with_uv_singularity_state(
                self._with_registration_state(MoonTileDiagnostics.inactive("below_tiled_threshold")),
                draw_state,
            )
            self.last_diagnostics = diagnostics
            stop_benchmark(diagnostics)
            return diagnostics

        target_level = self.tile_lod_level_for_diameter_px(diameter_px)
        if target_level < 0:
            diagnostics = budget_fallback("moon_tile_directory_unavailable")
            stop_benchmark(diagnostics)
            return diagnostics
        if budget_exceeded():
            diagnostics = budget_fallback("render_path_budget_global_fallback", target_level=target_level)
            stop_benchmark(diagnostics)
            return diagnostics
        tile_columns, tile_rows = self.tile_grid_dimensions(target_level)
        visible_albedo_keys = self.select_visible_tile_keys(
            draw_state,
            moon_state,
            target_level,
            kind="albedo",
            max_tiles=max(1, tile_columns * tile_rows),
            prioritize_terminator=bool(normal_maps_active),
        )
        requested_albedo_keys = tuple(visible_albedo_keys[: max(1, int(self.settings.max_visible_tiles))])
        shader_albedo_keys = self.coverage_tile_keys_for_bindings(
            requested_albedo_keys,
            sampler_limit=max(1, int(self.settings.shader_sampler_limit)),
        )
        self._last_requested_albedo_keys = requested_albedo_keys
        self._last_shader_albedo_keys = shader_albedo_keys
        requested_keys = list(dict.fromkeys((*requested_albedo_keys, *shader_albedo_keys)))
        if normal_maps_active:
            requested_keys.extend(
                MoonTileKey("normal", key.lod_level, key.tile_x, key.tile_y)
                for key in shader_albedo_keys
                if MoonTileKey("normal", key.lod_level, key.tile_x, key.tile_y) in self._available_tiles
            )
        protected_keys = set(requested_keys)
        for key in requested_keys:
            self._request_tile(key)
        if tile_poll_limit is None:
            self._poll_tile_futures()
        deadline = perf_counter() + max(0.0001, float(self.settings.render_path_budget_seconds))
        if budget_exceeded():
            diagnostics = budget_fallback("render_path_budget_global_fallback", target_level=target_level)
            stop_benchmark(diagnostics)
            return diagnostics

        resident_count = 0
        lower_resolution_count = 0
        missing_count = 0
        for key in requested_albedo_keys:
            if key in self._resident_tiles:
                resident_count += 1
                self._resident_tiles.move_to_end(key)
                continue
            lower_tile = self.best_resident_fallback_tile(key)
            if lower_tile is not None:
                resident_count += 1
                lower_resolution_count += 1
                protected_keys.add(lower_tile.key)
            else:
                missing_count += 1
        self._evict_tile_cache(protected_keys=protected_keys)
        pending_count = sum(1 for key in requested_keys if key in self._tile_futures)
        if missing_count == 0 and lower_resolution_count == 0:
            fallback_reason = "tiles_ready_fixed_shader_sampling"
        elif pending_count > 0:
            fallback_reason = "tiles_pending_global_fallback"
        elif lower_resolution_count > 0:
            fallback_reason = "using_lower_resolution_tiles"
        else:
            fallback_reason = "missing_high_zoom_tiles_global_fallback"
        shader_bindings = self._candidate_shader_bindings(
            normal_maps_active=bool(normal_maps_active and not self.settings.disable_tiled_normals),
        )
        coverage_report = self._coverage_report(draw_state, shader_bindings)
        if budget_exceeded():
            diagnostics = budget_fallback("render_path_budget_global_fallback", target_level=target_level)
            stop_benchmark(diagnostics)
            return diagnostics
        visible_coverage = float(coverage_report["coverage"])
        equivalence_report = self._live_equivalence_report(shader_bindings, global_albedo_lod)
        require_safe_activation = bool(self.settings.require_safe_live_activation and self.settings.enforce_source_registration)
        coverage_passes = visible_coverage >= max(0.0, min(1.0, float(self.settings.minimum_visible_coverage)))
        equivalence_passes = bool(equivalence_report["passed"])
        generation_passes = all(
            binding.albedo_tile.generation_id == self._tile_cache_generation_id for binding in shader_bindings
        )
        rejected_due_to_coverage = bool(require_safe_activation and shader_bindings and not coverage_passes)
        rejected_due_to_mismatch = bool(require_safe_activation and shader_bindings and (not equivalence_passes or not generation_passes))
        activation_warning = ""
        if rejected_due_to_coverage:
            activation_warning = self.COVERAGE_WARNING
        elif rejected_due_to_mismatch:
            activation_warning = self.LIVE_EQUIVALENCE_WARNING
        if rejected_due_to_coverage or rejected_due_to_mismatch:
            self._tiled_mode_active = False
            self._active_shader_bindings = ()
            shader_fallback_reason = "unsafe_live_tiled_activation_global_fallback"
            active_visible_coverage = 0.0
            active_fallback_coverage = 1.0
            bound_shader_bindings: tuple[MoonTileShaderBinding, ...] = ()
        else:
            self._active_shader_bindings = tuple(shader_bindings)
            shader_fallback_reason = "fixed_tile_shader_ready" if shader_bindings else "no_resident_shader_tiles_global_fallback"
            active_visible_coverage = visible_coverage if shader_bindings else 0.0
            active_fallback_coverage = max(0.0, min(1.0, 1.0 - active_visible_coverage))
            bound_shader_bindings = tuple(shader_bindings)
        normal_report = self._normal_tile_diagnostics(
            draw_state,
            bound_shader_bindings if normal_maps_active else (),
            global_normal_lod,
        )
        diagnostics = MoonTileDiagnostics(
            tiled_mode_active=bool(self._tiled_mode_active),
            tile_lod_level=target_level,
            requested_tile_count=len(requested_albedo_keys),
            resident_tile_count=resident_count,
            missing_tile_count=missing_count,
            fallback_reason=fallback_reason,
            tile_cache_memory_estimate=self._tile_cache_memory_estimate,
            pending_tile_count=pending_count,
            lower_resolution_tile_count=lower_resolution_count,
            available_tile_count=len(available_albedo_keys),
            visible_tile_count=len(visible_albedo_keys),
            selected_tile_count=len(requested_albedo_keys),
            selected_tile_keys=tuple(self._format_tile_key(key) for key in requested_albedo_keys),
            tiled_shader_active=bool(bound_shader_bindings),
            bound_tile_count=len(bound_shader_bindings),
            bound_albedo_tile_count=len(bound_shader_bindings),
            bound_normal_tile_count=sum(1 for binding in bound_shader_bindings if binding.normal_tile is not None),
            shader_tile_fallback_reason=shader_fallback_reason,
            tile_edge_fade_enabled=self.settings.tile_edge_fade_fraction > 0.0,
            tile_sampler_limit=max(1, int(self.settings.shader_sampler_limit)),
            max_tile_samplers=max(1, int(self.settings.shader_sampler_limit)),
            visible_coverage_estimate=active_visible_coverage,
            fallback_coverage_estimate=active_fallback_coverage,
            selected_bound_tile_keys=tuple(self._format_tile_key(binding.key) for binding in bound_shader_bindings),
            activation_warning=activation_warning,
            cache_generation_matches_manifest=generation_passes,
            bound_tile_source_hash=self._tile_source_id if bound_shader_bindings else "",
            bound_tile_convention_hash=self._tile_convention_hash if bound_shader_bindings else "",
            bound_tile_keys=tuple(self._format_tile_key(binding.key) for binding in bound_shader_bindings),
            bound_tile_uv_bounds=tuple(coverage_report["bounds"]),
            bound_tile_texture_predates_current_manifest=any(
                binding.albedo_tile.generation_id != self._tile_cache_generation_id for binding in shader_bindings
            ),
            visible_uv_footprint=str(coverage_report["footprint"]),
            candidate_visible_coverage_estimate=visible_coverage,
            candidate_global_fallback_estimate=float(coverage_report["fallback"]),
            visible_uv_sample_count=int(coverage_report["sample_count"]),
            any_out_of_bounds_tile_sampling=bool(coverage_report["out_of_bounds_sampling"]),
            live_equivalence_status=str(equivalence_report["status"]),
            live_probe_mean_color_delta=equivalence_report["mean_delta"],
            live_probe_max_color_delta=equivalence_report["max_delta"],
            live_landmark_sample_deltas=tuple(equivalence_report["landmarks"]),
            tiles_rejected_due_to_unsafe_coverage=rejected_due_to_coverage,
            tiles_rejected_due_to_live_mismatch=rejected_due_to_mismatch,
            tiled_normal_strength=float(draw_state.tiled_normal_strength),
            tiled_normal_fade_factor=float(draw_state.tiled_normal_fade_factor),
            terminator_normal_factor=float(draw_state.terminator_normal_factor),
            problematic_tile_key=str(normal_report["problematic_tile_key"]),
            max_normal_slope=normal_report["max_slope"],
            normal_artifact_risk=bool(normal_report["risk"]),
            normal_tile_statistics=tuple(normal_report["statistics"]),
            artifact_lunar_latitude_deg=coverage_report["artifact_latitude_deg"],
            artifact_lunar_longitude_deg=coverage_report["artifact_longitude_deg"],
            artifact_disc_position=str(coverage_report["artifact_disc_position"]),
            visible_uv_near_north_pole=bool(coverage_report["near_north_pole"]),
            visible_uv_near_south_pole=bool(coverage_report["near_south_pole"]),
            visible_uv_crosses_longitude_seam=bool(coverage_report["crosses_seam"]),
            visible_pole_proximity=float(coverage_report["pole_proximity"]),
            visible_seam_proximity=float(coverage_report["seam_proximity"]),
            polar_normal_fade_factor=float(coverage_report["polar_normal_fade_factor"]),
            polar_albedo_fade_factor=float(coverage_report["polar_albedo_fade_factor"]),
            polar_analytic_normal_active=bool(
                DEFAULT_MOON_VISUAL_SETTINGS.polar_use_analytic_normal
                and float(coverage_report["polar_normal_fade_factor"]) >= 1.0 - 1.0e-6
            ),
            seam_wrap_active=True,
            pole_singularity_guard_active=bool(
                float(coverage_report["polar_normal_fade_factor"]) > 0.0
                or float(coverage_report["polar_albedo_fade_factor"]) > 0.0
            ),
        )
        diagnostics = self._with_registration_state(diagnostics)
        self.last_diagnostics = diagnostics
        stop_benchmark(diagnostics)
        return diagnostics

    def _candidate_shader_bindings(
        self,
        *,
        normal_maps_active: bool = False,
        sampler_limit: int | None = None,
    ) -> tuple[MoonTileShaderBinding, ...]:

        limit = max(1, int(self.settings.shader_sampler_limit if sampler_limit is None else sampler_limit))
        candidates: list[tuple[float, int, MoonTextureTile, MoonTextureTile | None]] = []
        seen_albedo_keys: set[MoonTileKey] = set()
        for order, requested_key in enumerate(self._last_shader_albedo_keys or self._last_requested_albedo_keys):
            albedo_tile = self._resident_tiles.get(requested_key) or self.best_resident_fallback_tile(requested_key)
            if albedo_tile is None or albedo_tile.key in seen_albedo_keys:
                continue
            seen_albedo_keys.add(albedo_tile.key)
            normal_tile = None
            if normal_maps_active:
                normal_tile = self._resident_tiles.get(MoonTileKey("normal", albedo_tile.key.lod_level, albedo_tile.key.tile_x, albedo_tile.key.tile_y))
            fallback_penalty = max(0, requested_key.lod_level - albedo_tile.key.lod_level) * 0.25
            normal_penalty = 0.08 if normal_maps_active and normal_tile is None else 0.0
            candidates.append((float(order) + fallback_penalty + normal_penalty, order, albedo_tile, normal_tile))
        candidates.sort(key=lambda item: (item[0], item[1], -item[2].key.lod_level, item[2].key.tile_y, item[2].key.tile_x))
        return tuple(MoonTileShaderBinding(albedo_tile, normal_tile) for _priority, _order, albedo_tile, normal_tile in candidates[:limit])

    def shader_bindings(
        self,
        *,
        normal_maps_active: bool = False,
        sampler_limit: int | None = None,
    ) -> tuple[MoonTileShaderBinding, ...]:

        _ = normal_maps_active
        limit = max(1, int(self.settings.shader_sampler_limit if sampler_limit is None else sampler_limit))
        return tuple(self._active_shader_bindings[:limit])

    def _load_polar_cap_lod(self, path: Path | None, label: str) -> MoonTextureLod | None:

        if path is None or not path.is_file():
            return None
        image = QImage(str(path)).convertToFormat(QImage.Format.Format_RGB888)
        if image.isNull():
            return None
        stat = path.stat()
        return MoonTextureLod(
            label=label,
            width=int(image.width()),
            height=int(image.height()),
            image=image,
            cache_key=(
                "moon-polar-cap",
                self._tile_cache_generation_id,
                str(path),
                int(stat.st_mtime_ns),
                int(stat.st_size),
            ),
        )

    def polar_cap_binding(self, *, normal_maps_active: bool = False) -> MoonPolarCapBinding | None:

        self._request_generation_refresh()
        if self._tiles_rejected_due_to_registration_mismatch:
            self._active_polar_cap_binding = None
            return None
        cache_key = bool(normal_maps_active)
        future = self._polar_cap_binding_futures.get(cache_key)
        if future is not None and future.done():
            self._polar_cap_binding_futures.pop(cache_key, None)
            try:
                binding = future.result()
            except Exception:
                binding = None
            self._polar_cap_binding_cache[cache_key] = binding
            if binding is not None:
                self._active_polar_cap_binding = binding
        if cache_key in self._polar_cap_binding_cache:
            binding = self._polar_cap_binding_cache[cache_key]
            if binding is not None:
                self._active_polar_cap_binding = binding
            return binding
        if future is None and self._generation_signature is not None:
            self._polar_cap_binding_futures[cache_key] = self._executor.submit(
                self._compute_polar_cap_binding,
                bool(normal_maps_active),
                dict(self._polar_caps_manifest),
                self._manifest_hash,
                self._tile_source_id,
                self._tile_cache_generation_id,
            )
            future = self._polar_cap_binding_futures[cache_key]
            if future.done():
                self._polar_cap_binding_futures.pop(cache_key, None)
                try:
                    binding = future.result()
                except Exception:
                    binding = None
                self._polar_cap_binding_cache[cache_key] = binding
                if binding is not None:
                    self._active_polar_cap_binding = binding
                return binding
        return None

    def _compute_polar_cap_binding(
        self,
        normal_maps_active: bool,
        caps_manifest: Mapping[str, object],
        manifest_hash: str,
        tile_source_id: str,
        generation_id: str,
    ) -> MoonPolarCapBinding | None:

        if not isinstance(caps_manifest, dict) or not bool(caps_manifest.get("enabled", False)):
            return None
        validation = caps_manifest.get("validation") if isinstance(caps_manifest.get("validation"), dict) else {}
        if validation and str(validation.get("status") or "") not in {"pass", "pending"}:
            return None
        caps = caps_manifest.get("caps") if isinstance(caps_manifest.get("caps"), dict) else {}
        cap_validation = caps_manifest.get("validation") if isinstance(caps_manifest.get("validation"), dict) else {}
        validated_caps = cap_validation.get("caps") if isinstance(cap_validation.get("caps"), dict) else {}
        filtering_manifest = caps_manifest.get("filtering_policy") if isinstance(caps_manifest.get("filtering_policy"), dict) else {}
        blend_curve = caps_manifest.get("blend_curve") if isinstance(caps_manifest.get("blend_curve"), dict) else {}
        per_pole_metrics: dict[str, dict[str, object]] = {}
        for pole in ("north", "south"):
            validated_pole = validated_caps.get(pole) if isinstance(validated_caps.get(pole), dict) else {}
            pole_footprint = validated_pole.get("footprint_metrics") if isinstance(validated_pole.get("footprint_metrics"), dict) else {}
            pole_boundary = validated_pole.get("boundary_metrics") if isinstance(validated_pole.get("boundary_metrics"), dict) else {}
            per_pole_metrics[pole] = {
                "boundary_luma_delta": pole_boundary.get("mean_luminance_delta"),
                "boundary_color_delta": pole_boundary.get("mean_color_delta"),
                "boundary_contrast_delta": pole_boundary.get("contrast_delta"),
                "core_sharpness_estimate": pole_footprint.get("core_sharpness_estimate"),
                "core_vs_surrounding_detail_delta": pole_footprint.get("core_vs_surrounding_detail_delta"),
                "footprint_risk": validated_pole.get("footprint_risk", False),
            }

        def resolved_path(pole: str, kind: str) -> Path | None:

            cap = caps.get(pole) if isinstance(caps.get(pole), dict) else {}
            path_value = cap.get(f"{kind}_path") if isinstance(cap, dict) else None
            return self.albedo_tile_root.parent / str(path_value) if path_value else None

        north_albedo = self._load_polar_cap_lod(resolved_path("north", "albedo"), "polar-cap-north-albedo")
        south_albedo = self._load_polar_cap_lod(resolved_path("south", "albedo"), "polar-cap-south-albedo")
        if north_albedo is None and south_albedo is None:
            self._active_polar_cap_binding = None
            return None
        binding = MoonPolarCapBinding(
            projection=str(caps_manifest.get("projection") or ""),
            latitude_coverage_deg=float(caps_manifest.get("latitude_coverage_deg", DEFAULT_MOON_VISUAL_SETTINGS.polar_cap_latitude_coverage_deg)),
            blend_start_lat_deg=float(caps_manifest.get("blend_start_lat_deg", DEFAULT_MOON_VISUAL_SETTINGS.polar_cap_blend_start_lat_deg)),
            blend_end_lat_deg=float(caps_manifest.get("blend_end_lat_deg", DEFAULT_MOON_VISUAL_SETTINGS.polar_cap_blend_end_lat_deg)),
            texture_size_px=int(caps_manifest.get("texture_size", 0) or 0),
            blend_curve_power=float(blend_curve.get("power", DEFAULT_MOON_VISUAL_SETTINGS.polar_cap_blend_curve_power)),
            normal_blend_start_lat_deg=float(caps_manifest.get("normal_blend_start_lat_deg", DEFAULT_MOON_VISUAL_SETTINGS.polar_cap_normal_blend_start_lat_deg)),
            normal_blend_end_lat_deg=float(caps_manifest.get("normal_blend_end_lat_deg", DEFAULT_MOON_VISUAL_SETTINGS.polar_cap_normal_blend_end_lat_deg)),
            boundary_luma_delta=(
                float(cap_validation["boundary_mean_luminance_delta"])
                if cap_validation.get("boundary_mean_luminance_delta") is not None
                else None
            ),
            boundary_color_delta=(
                float(cap_validation["boundary_mean_color_delta"])
                if cap_validation.get("boundary_mean_color_delta") is not None
                else None
            ),
            boundary_contrast_delta=(
                float(cap_validation["boundary_mean_contrast_delta"])
                if cap_validation.get("boundary_mean_contrast_delta") is not None
                else None
            ),
            core_sharpness_estimate=(
                float(cap_validation["core_sharpness_estimate"])
                if cap_validation.get("core_sharpness_estimate") is not None
                else None
            ),
            core_vs_surrounding_detail_delta=(
                float(cap_validation["core_vs_surrounding_detail_delta"])
                if cap_validation.get("core_vs_surrounding_detail_delta") is not None
                else None
            ),
            filtering_policy=(
                f"{filtering_manifest.get('magnification', 'linear')}/{filtering_manifest.get('minification', 'linear_mipmap_linear')}; "
                f"{filtering_manifest.get('wrap_s', 'clamp_to_edge')}"
            ),
            footprint_risk=bool(cap_validation.get("footprint_risk", False)),
            cap_metrics=per_pole_metrics,
            north_albedo=north_albedo,
            south_albedo=south_albedo,
            north_normal=self._load_polar_cap_lod(resolved_path("north", "normal"), "polar-cap-north-normal") if normal_maps_active else None,
            south_normal=self._load_polar_cap_lod(resolved_path("south", "normal"), "polar-cap-south-normal") if normal_maps_active else None,
            manifest_hash=manifest_hash,
            source_hash=tile_source_id,
            generation_id=generation_id,
        )
        return binding

    def coverage_tile_keys_for_bindings(
        self,
        requested_albedo_keys: Sequence[MoonTileKey],
        *,
        sampler_limit: int,
    ) -> tuple[MoonTileKey, ...]:

        requested_keys = tuple(key for key in requested_albedo_keys if key.kind == "albedo")
        if not requested_keys:
            return ()
        limit = max(1, int(sampler_limit))
        target_level = max(key.lod_level for key in requested_keys)
        available_levels = sorted({key.lod_level for key in self._available_tiles if key.kind == "albedo" and key.lod_level <= target_level}, reverse=True)
        if not available_levels:
            available_levels = list(range(target_level, -1, -1))
        best_keys: tuple[MoonTileKey, ...] = requested_keys[:limit]
        for candidate_level in available_levels:
            candidate_keys: list[MoonTileKey] = []
            seen: set[MoonTileKey] = set()
            for key in requested_keys:
                candidate = key if key.lod_level == candidate_level else self.parent_tile_key(key, candidate_level)
                if candidate in seen:
                    continue
                seen.add(candidate)
                candidate_keys.append(candidate)
            if len(candidate_keys) <= limit:
                return tuple(candidate_keys)
            best_keys = tuple(candidate_keys[:limit])
        return best_keys

    @classmethod
    def tile_key_covers(cls, candidate_key: MoonTileKey, target_key: MoonTileKey) -> bool:

        if candidate_key.kind != target_key.kind or candidate_key.lod_level > target_key.lod_level:
            return False
        if candidate_key.lod_level == target_key.lod_level:
            return candidate_key.tile_x == target_key.tile_x and candidate_key.tile_y == target_key.tile_y
        return cls.parent_tile_key(target_key, candidate_key.lod_level) == candidate_key

    @classmethod
    def estimate_binding_coverage(
        cls,
        visible_albedo_keys: Sequence[MoonTileKey],
        tile_bindings: Sequence[MoonTileShaderBinding],
    ) -> float:

        visible_keys = tuple(key for key in visible_albedo_keys if key.kind == "albedo")
        if not visible_keys:
            return 0.0
        bound_keys = tuple(binding.key for binding in tile_bindings)
        if not bound_keys:
            return 0.0
        covered = sum(1 for key in visible_keys if any(cls.tile_key_covers(bound_key, key) for bound_key in bound_keys))
        return max(0.0, min(1.0, covered / max(1, len(visible_keys))))

    def tile_lod_level_for_diameter_px(self, diameter_px: float) -> int:

        available_levels = sorted({key.lod_level for key in self._available_tiles if key.kind == "albedo"})
        if not available_levels:
            return -1
        threshold = max(1.0, float(self.settings.activation_diameter_px))
        level_offset = max(0, int(math.floor(math.log2(max(1.0, float(diameter_px)) / threshold))))
        return int(available_levels[min(level_offset, len(available_levels) - 1)])

    def select_visible_tile_keys(
        self,
        draw_state: MoonDrawState,
        moon_state: MoonState,
        lod_level: int,
        *,
        kind: str = "albedo",
        max_tiles: int | None = None,
        prioritize_terminator: bool = False,
    ) -> tuple[MoonTileKey, ...]:

        tile_columns, tile_rows = self.tile_grid_dimensions(lod_level)
        grid_samples = max(3, int(self.settings.selection_grid_samples))
        if grid_samples % 2 == 0:
            grid_samples += 1
        sample_values = [
            -1.0 + 2.0 * sample_index / max(1, grid_samples - 1)
            for sample_index in range(grid_samples)
        ]
        sample_points = [(0.0, 0.0)]
        sample_points.extend((sample_x, sample_y) for sample_y in sample_values for sample_x in sample_values)
        priorities: dict[MoonTileKey, float] = {}
        sun_direction = self._normalize_vector(moon_state.sun_dir_moon_local)
        for sample_x, sample_y in sample_points:
            radius_squared = sample_x * sample_x + sample_y * sample_y
            if radius_squared > 1.0:
                continue
            normal_view = (float(sample_x), float(sample_y), math.sqrt(max(0.0, 1.0 - radius_squared)))
            normal_moon = self._normalize_vector(
                (
                    self._dot(draw_state.view_to_moon_matrix[0], normal_view),
                    self._dot(draw_state.view_to_moon_matrix[1], normal_view),
                    self._dot(draw_state.view_to_moon_matrix[2], normal_view),
                )
            )
            texture_u, texture_v = self.moon_normal_to_uv(normal_moon)
            tile_x = min(tile_columns - 1, max(0, int(texture_u * tile_columns)))
            tile_y = min(tile_rows - 1, max(0, int(texture_v * tile_rows)))
            key = MoonTileKey(kind, int(lod_level), tile_x, tile_y)
            center_distance = math.sqrt(radius_squared)
            terminator_distance = abs(self._dot(normal_moon, sun_direction))
            priority = center_distance if not prioritize_terminator else center_distance * 0.68 + terminator_distance * 0.32
            priorities[key] = min(priorities.get(key, float("inf")), priority)
        limit = max(1, int(max_tiles if max_tiles is not None else self.settings.max_visible_tiles))
        return tuple(
            key
            for key, _priority in sorted(priorities.items(), key=lambda item: (item[1], item[0].lod_level, item[0].tile_y, item[0].tile_x))[:limit]
        )

    def best_resident_fallback_tile(self, key: MoonTileKey) -> MoonTextureTile | None:

        candidate_level = int(key.lod_level) - 1
        while candidate_level >= 0:
            parent_key = self.parent_tile_key(key, candidate_level)
            tile = self._resident_tiles.get(parent_key)
            if tile is not None:
                self._resident_tiles.move_to_end(parent_key)
                return tile
            candidate_level -= 1
        return None

    @classmethod
    def parent_tile_key(cls, key: MoonTileKey, parent_lod_level: int) -> MoonTileKey:

        parent_columns, parent_rows = cls.tile_grid_dimensions(parent_lod_level)
        child_columns, child_rows = cls.tile_grid_dimensions(key.lod_level)
        parent_x = min(parent_columns - 1, int(math.floor((key.tile_x + 0.5) * parent_columns / max(1, child_columns))))
        parent_y = min(parent_rows - 1, int(math.floor((key.tile_y + 0.5) * parent_rows / max(1, child_rows))))
        return MoonTileKey(key.kind, int(parent_lod_level), parent_x, parent_y)

    @staticmethod
    def tile_grid_dimensions(lod_level: int) -> tuple[int, int]:

        level = max(0, int(lod_level))
        return 2 ** (level + 1), 2**level

    @staticmethod
    def moon_normal_to_uv(normal_moon: Vector3) -> tuple[float, float]:

        normalized = MoonTiledTextureManager._normalize_vector(normal_moon)
        longitude_rad = math.atan2(normalized[0], normalized[2])
        latitude_rad = math.asin(max(-1.0, min(1.0, normalized[1])))
        return (0.5 + longitude_rad / (2.0 * math.pi)) % 1.0, max(0.0, min(1.0, 0.5 - latitude_rad / math.pi))

    @classmethod
    def tile_uv_bounds(cls, key: MoonTileKey) -> MoonTileUvBounds:

        tile_columns, tile_rows = cls.tile_grid_dimensions(key.lod_level)
        return MoonTileUvBounds(
            u_min=float(key.tile_x) / float(tile_columns),
            v_min=float(key.tile_y) / float(tile_rows),
            u_max=float(key.tile_x + 1) / float(tile_columns),
            v_max=float(key.tile_y + 1) / float(tile_rows),
        )

    def has_pending_tile_work(self) -> bool:

        return any(not future.done() for future in self._tile_futures.values())

    def _render_tile_future_poll_limit(self) -> int | None:

        return 2 if isinstance(self._executor, ThreadPoolExecutor) else None

    def _ensure_tile_index(self) -> None:

        if self._tile_index_built:
            return
        self._available_tiles.update(self._scan_tile_root(self.albedo_tile_root, "albedo"))
        self._available_tiles.update(self._scan_tile_root(self.normal_tile_root, "normal"))
        self._tile_index_built = True

    def _scan_tile_root(self, root: Path, kind: str) -> dict[MoonTileKey, Path]:

        if not root.is_dir():
            return {}
        tiles: dict[MoonTileKey, Path] = {}
        for lod_dir in sorted(path for path in root.iterdir() if path.is_dir()):
            lod_level = self._parse_lod_level(lod_dir.name)
            if lod_level is None:
                continue
            for path in sorted(lod_dir.iterdir()):
                if not path.is_file() or path.suffix.casefold() not in self.SUPPORTED_SUFFIXES:
                    continue
                parsed_tile = self._parse_tile_xy(path.stem)
                if parsed_tile is None:
                    continue
                tile_x, tile_y = parsed_tile
                tile_columns, tile_rows = self.tile_grid_dimensions(lod_level)
                if 0 <= tile_x < tile_columns and 0 <= tile_y < tile_rows:
                    tiles[MoonTileKey(kind, lod_level, tile_x, tile_y)] = path
        return tiles

    def _request_tile(self, key: MoonTileKey) -> None:

        if key in self._resident_tiles or key in self._tile_futures:
            return
        path = self._available_tiles.get(key)
        if path is None:
            return
        self._tile_futures[key] = self._executor.submit(
            self._load_tile,
            key,
            path,
            self._tile_cache_generation_id,
            self._tile_source_id,
            self._tile_convention_hash,
        )

    def _poll_tile_futures(self, *, max_results: int | None = None) -> None:

        finished_keys = [key for key, future in self._tile_futures.items() if future.done()]
        if max_results is not None:
            finished_keys = finished_keys[: max(0, int(max_results))]
        for key in finished_keys:
            future = self._tile_futures.pop(key)
            try:
                tile = future.result()
            except Exception:
                tile = None
            if tile is None:
                continue
            old_tile = self._resident_tiles.pop(key, None)
            if old_tile is not None:
                self._tile_cache_memory_estimate = max(0, self._tile_cache_memory_estimate - old_tile.approx_bytes)
            self._resident_tiles[key] = tile
            self._resident_tiles.move_to_end(key)
            self._tile_cache_memory_estimate += tile.approx_bytes

    def _evict_tile_cache(self, *, protected_keys: set[MoonTileKey]) -> None:

        budget = max(1, int(self.settings.cache_budget_bytes))
        while self._resident_tiles and self._tile_cache_memory_estimate > budget:
            evict_key = None
            for candidate_key in self._resident_tiles:
                if candidate_key not in protected_keys:
                    evict_key = candidate_key
                    break
            if evict_key is None:
                break
            tile = self._resident_tiles.pop(evict_key)
            self._tile_cache_memory_estimate = max(0, self._tile_cache_memory_estimate - tile.approx_bytes)

    @classmethod
    def _load_tile(
        cls,
        key: MoonTileKey,
        path: Path,
        generation_id: str = "",
        source_hash: str = "",
        convention_hash: str = "",
    ) -> MoonTextureTile | None:

        benchmark_recorder = get_benchmark_recorder() if BENCHMARK_ENABLED else None

        benchmark_token = (

            benchmark_recorder.start_section(

                "moon.tile_load",

                metadata={"kind": key.kind, "lod": key.lod_level, "tile_x": key.tile_x, "tile_y": key.tile_y},

            )

            if benchmark_recorder is not None

            else None

        )

        source_path = Path(path)
        if not source_path.is_file():
            if benchmark_recorder is not None:
                benchmark_recorder.stop_section(benchmark_token, metadata={"status": "missing"})
            return None
        image = QImage(str(source_path))
        if image.isNull():
            if benchmark_recorder is not None:
                benchmark_recorder.stop_section(benchmark_token, metadata={"status": "decode-error"})
            return None
        image = image.convertToFormat(QImage.Format.Format_RGB888)
        stat = source_path.stat()
        tile = MoonTextureTile(
            key=key,
            label=cls._format_tile_key(key),
            path=source_path,
            uv_bounds=cls.tile_uv_bounds(key),
            image=image,
            cache_key=(
                "moon-tile",
                str(generation_id),
                key.kind,
                int(key.lod_level),
                int(key.tile_x),
                int(key.tile_y),
                str(source_path),
                int(stat.st_mtime_ns),
                int(stat.st_size),
                int(image.width()),
                int(image.height()),
            ),
            generation_id=str(generation_id),
            source_hash=str(source_hash),
            convention_hash=str(convention_hash),
        )
        if benchmark_recorder is not None:
            benchmark_recorder.stop_section(
                benchmark_token,
                metadata={"status": "ok", "width": int(image.width()), "height": int(image.height()), "bytes": tile.approx_bytes},
            )
        return tile

    @staticmethod
    def _parse_lod_level(value: str) -> int | None:

        text = value.strip().casefold()
        if text.startswith("l"):
            text = text[1:]
        if not text.isdigit():
            return None
        return int(text)

    @staticmethod
    def _parse_tile_xy(value: str) -> tuple[int, int] | None:

        normalized = value.replace("-", "_").replace(".", "_")
        parts = [part for part in normalized.split("_") if part]
        if len(parts) != 2 or not all(part.isdigit() for part in parts):
            return None
        return int(parts[0]), int(parts[1])

    @staticmethod
    def _format_tile_key(key: MoonTileKey) -> str:

        return f"{key.kind}:L{key.lod_level}/{key.tile_x}_{key.tile_y}"

    @staticmethod
    def _dot(lhs: Vector3, rhs: Vector3) -> float:

        return lhs[0] * rhs[0] + lhs[1] * rhs[1] + lhs[2] * rhs[2]

    @staticmethod
    def _normalize_vector(vector: Vector3) -> Vector3:

        length = math.sqrt(vector[0] * vector[0] + vector[1] * vector[1] + vector[2] * vector[2])
        if length <= 1.0e-12:
            return (0.0, 0.0, 1.0)
        return (vector[0] / length, vector[1] / length, vector[2] / length)


def _moon_fragment_visual_constant_source(settings: MoonVisualSettings) -> str:

    return (
        f"const float MOON_TERMINATOR_SOFT_START = {settings.terminator_soft_start:.6f};\n"
        f"const float MOON_TERMINATOR_SOFT_END = {settings.terminator_soft_end:.6f};\n"
        f"const float MOON_TERRAIN_TERMINATOR_START = {settings.terrain_terminator_start:.6f};\n"
        f"const float MOON_TERRAIN_TERMINATOR_PEAK_END = {settings.terrain_terminator_peak_end:.6f};\n"
        f"const float MOON_TERRAIN_TERMINATOR_FADE_START = {settings.terrain_terminator_fade_start:.6f};\n"
        f"const float MOON_TERRAIN_TERMINATOR_FADE_END = {settings.terrain_terminator_fade_end:.6f};\n"
        f"const float MOON_TERRAIN_BLEND_CAP = {settings.terrain_blend_cap:.6f};\n"
        f"const float MOON_TERMINATOR_BAND_WIDTH = {settings.terminator_band_width:.6f};\n"
        f"const float MOON_TERMINATOR_SOFTNESS = {settings.terminator_softness:.6f};\n"
        f"const float MOON_TERMINATOR_NORMAL_LOW_LIGHT_START = {settings.terminator_normal_low_light_start:.6f};\n"
        f"const float MOON_TERMINATOR_NORMAL_LOW_LIGHT_END = {settings.terminator_normal_low_light_end:.6f};\n"
        f"const float MOON_TERMINATOR_NORMAL_MIN_FACTOR = {settings.terminator_normal_min_factor:.6f};\n"
        f"const float MOON_POLAR_NORMAL_FADE_START_LAT_DEG = {settings.polar_normal_fade_start_lat_deg:.6f};\n"
        f"const float MOON_POLAR_NORMAL_FADE_END_LAT_DEG = {settings.polar_normal_fade_end_lat_deg:.6f};\n"
        f"const float MOON_POLAR_ALBEDO_FADE_START_LAT_DEG = {settings.polar_albedo_fade_start_lat_deg:.6f};\n"
        f"const float MOON_POLAR_ALBEDO_FADE_END_LAT_DEG = {settings.polar_albedo_fade_end_lat_deg:.6f};\n"
        f"const float MOON_POLAR_USE_ANALYTIC_NORMAL = {1.0 if settings.polar_use_analytic_normal else 0.0:.6f};\n"
        f"const float MOON_SEAM_DIAGNOSTIC_WIDTH_UV = {settings.seam_diagnostic_width_uv:.6f};\n"
        f"const float MOON_POLAR_CAP_NORMAL_STRENGTH = {settings.polar_cap_normal_strength:.6f};\n"
        f"const float MOON_POLAR_CAP_BLEND_START_LAT_DEG = {settings.polar_cap_blend_start_lat_deg:.6f};\n"
        f"const float MOON_POLAR_CAP_BLEND_END_LAT_DEG = {settings.polar_cap_blend_end_lat_deg:.6f};\n"
        f"const float MOON_POLAR_CAP_BLEND_CURVE_POWER = {settings.polar_cap_blend_curve_power:.6f};\n"
        f"const float MOON_POLAR_CAP_NORMAL_BLEND_START_LAT_DEG = {settings.polar_cap_normal_blend_start_lat_deg:.6f};\n"
        f"const float MOON_POLAR_CAP_NORMAL_BLEND_END_LAT_DEG = {settings.polar_cap_normal_blend_end_lat_deg:.6f};\n"
        f"const float MOON_POLAR_CAP_LATITUDE_COVERAGE_DEG = {settings.polar_cap_latitude_coverage_deg:.6f};\n"
        f"const float MOON_TERRAIN_NORMAL_Z_FLOOR = {settings.terrain_normal_z_floor:.6f};\n"
        f"const float MOON_EARTHSHINE_SHADOW_START = {settings.earthshine_shadow_start:.6f};\n"
        f"const float MOON_EARTHSHINE_SHADOW_END = {settings.earthshine_shadow_end:.6f};\n"
        f"const float MOON_EARTHSHINE_MAX = {settings.earthshine_max:.6f};\n"
        f"const float MOON_EARTHSHINE_DIRECTIONAL_GAMMA = {settings.earthshine_directional_gamma:.6f};\n"
        f"const float MOON_LIMB_SOFTNESS = {settings.limb_softness:.6f};\n"
        f"const float MOON_DIRECT_LIGHT_GAIN = {settings.direct_light_gain:.6f};\n"
        f"const float MOON_LIGHTING_HEMISPHERE_BLEND = {settings.lighting_hemisphere_blend:.6f};\n"
        f"const float MOON_LIGHTING_HEMISPHERE_GAMMA = {settings.lighting_hemisphere_gamma:.6f};\n"
        f"const float MOON_LIGHTING_HIGHLIGHT_KNEE = {settings.lighting_highlight_knee:.6f};\n"
        f"const float MOON_LIGHTING_HIGHLIGHT_COMPRESSION = {settings.lighting_highlight_compression:.6f};\n"
        f"const float MOON_ALBEDO_BRIGHTNESS = {settings.albedo_brightness:.6f};\n"
        f"const float MOON_ALBEDO_CONTRAST = {settings.albedo_contrast:.6f};\n"
        f"const float MOON_ALBEDO_SATURATION_RETENTION = {settings.albedo_saturation_retention:.6f};\n"
        f"const float MOON_ALBEDO_MARE_HIGHLAND_CONTRAST_BOOST = {settings.mare_highland_contrast_boost:.6f};\n"
        f"const float MOON_TONE_EXPOSURE = {settings.tone_exposure:.6f};\n"
        f"const float MOON_TONE_HIGHLIGHT_KNEE = {settings.tone_highlight_knee:.6f};\n"
        f"const float MOON_TONE_HIGHLIGHT_COMPRESSION = {settings.tone_highlight_compression:.6f};\n"
        f"const float MOON_TONE_CONTRAST = {settings.tone_contrast:.6f};\n"
        f"const float MOON_TONE_PIVOT = {settings.tone_pivot:.6f};\n"
        f"const float MOON_TONE_SATURATION = {settings.tone_saturation:.6f};\n"
        f"const float MOON_TONE_COLOR_PRESERVATION = {settings.tone_color_preservation:.6f};\n"
        f"const float MOON_TONE_GAMMA = {settings.tone_gamma:.6f};\n"
    )


@dataclass(frozen=True, slots=True)
class MoonEphemerisValidationCase:

    label: str
    observation_time_utc: datetime
    latitude_deg: float
    longitude_deg: float
    elevation_m: float = 0.0


@dataclass(frozen=True, slots=True)
class MoonEphemerisValidationResult:

    case: MoonEphemerisValidationCase
    altitude_error_arcsec: float
    azimuth_error_arcsec: float
    angular_diameter_error_arcsec: float
    illuminated_fraction_error: float


@dataclass(frozen=True, slots=True)
class LunarOrientationComparisonCase:

    label: str
    observation_time_utc: datetime
    ra_deg: float
    dec_deg: float
    observer_latitude_deg: float
    observer_longitude_deg: float
    moon_to_sun_direction_equatorial: Vector3


@dataclass(frozen=True, slots=True)
class LunarOrientationComparisonResult:

    case: LunarOrientationComparisonCase
    max_body_axis_delta_deg: float
    libration_longitude_delta_deg: float
    libration_latitude_delta_deg: float
    lunar_north_position_angle_delta_deg: float


DEFAULT_LUNAR_ORIENTATION_COMPARISON_CASES: tuple[LunarOrientationComparisonCase, ...] = (
    LunarOrientationComparisonCase("thin crescent", datetime(2026, 5, 18, 3, 0, tzinfo=UTC), 67.0, 24.0, 31.5475, -99.3819, (-0.4, 0.2, 0.9)),
    LunarOrientationComparisonCase("first quarter", datetime(2026, 5, 23, 21, 0, tzinfo=UTC), 151.0, 12.0, 51.4769, 0.0, (0.3, -0.2, 0.9)),
    LunarOrientationComparisonCase("full low sky", datetime(2026, 5, 31, 12, 0, tzinfo=UTC), 244.0, -27.0, 35.6895, 139.6917, (0.1, 0.4, 0.9)),
)


def compare_lunar_orientation_providers(
    spice_provider: LunarOrientationProvider,
    *,
    approx_provider: LunarOrientationProvider | None = None,
    cases: Sequence[LunarOrientationComparisonCase] = DEFAULT_LUNAR_ORIENTATION_COMPARISON_CASES,
) -> tuple[LunarOrientationComparisonResult, ...]:

    approximate = approx_provider or LunarOrientationApprox()
    results: list[LunarOrientationComparisonResult] = []
    for case in cases:
        moon_direction = _equatorial_unit_vector(case.ra_deg, case.dec_deg)
        normalized_sun_direction = LunarOrientationApprox._normalize(case.moon_to_sun_direction_equatorial)
        approx_state = approximate.compute(
            case.observation_time_utc,
            ra_deg=case.ra_deg,
            dec_deg=case.dec_deg,
            observer_latitude_deg=case.observer_latitude_deg,
            observer_longitude_deg=case.observer_longitude_deg,
            moon_direction_equatorial=moon_direction,
            moon_to_sun_direction_equatorial=normalized_sun_direction,
        )
        spice_state = spice_provider.compute(
            case.observation_time_utc,
            ra_deg=case.ra_deg,
            dec_deg=case.dec_deg,
            observer_latitude_deg=case.observer_latitude_deg,
            observer_longitude_deg=case.observer_longitude_deg,
            moon_direction_equatorial=moon_direction,
            moon_to_sun_direction_equatorial=normalized_sun_direction,
        )
        results.append(
            LunarOrientationComparisonResult(
                case=case,
                max_body_axis_delta_deg=_matrix_axis_delta_deg(approx_state.moon_to_equatorial_matrix, spice_state.moon_to_equatorial_matrix),
                libration_longitude_delta_deg=math.degrees(_angle_distance_rad(approx_state.libration_longitude_rad, spice_state.libration_longitude_rad)),
                libration_latitude_delta_deg=abs(math.degrees(approx_state.libration_latitude_rad - spice_state.libration_latitude_rad)),
                lunar_north_position_angle_delta_deg=math.degrees(_angle_distance_rad(approx_state.lunar_north_position_angle_rad, spice_state.lunar_north_position_angle_rad)),
            )
        )
    return tuple(results)


def _equatorial_unit_vector(ra_deg: float, dec_deg: float) -> Vector3:

    ra_rad = math.radians(float(ra_deg) % 360.0)
    dec_rad = math.radians(max(-90.0, min(90.0, float(dec_deg))))
    cos_dec = math.cos(dec_rad)
    return (cos_dec * math.cos(ra_rad), cos_dec * math.sin(ra_rad), math.sin(dec_rad))


def _angle_distance_rad(lhs_rad: float, rhs_rad: float) -> float:

    return abs(((float(lhs_rad) - float(rhs_rad) + math.pi) % (2.0 * math.pi)) - math.pi)


def _matrix_axis_delta_deg(lhs: Matrix3, rhs: Matrix3) -> float:

    max_delta = 0.0
    for axis_index in range(3):
        lhs_axis = (lhs[0][axis_index], lhs[1][axis_index], lhs[2][axis_index])
        rhs_axis = (rhs[0][axis_index], rhs[1][axis_index], rhs[2][axis_index])
        dot_value = sum(lhs_axis[index] * rhs_axis[index] for index in range(3))
        max_delta = max(max_delta, math.degrees(math.acos(max(-1.0, min(1.0, dot_value)))))
    return max_delta


@dataclass(slots=True)
class _MoonTextureCacheEntry:

    texture: QOpenGLTexture
    width: int
    height: int
    approx_bytes: int


class MoonCache:

    DEFAULT_LOD_WIDTHS = (512, 1024, 2048, 4096, 8192)
    DEFAULT_COLOR_TEXTURE = Path("textures") / "moon_lroc_color_16bit_srgb_8k.tif"
    DEFAULT_DISPLACEMENT_TEXTURE = Path("textures") / "moon_ldem_16.tif"

    def __init__(
        self,
        project_root: Path | None = None,
        *,
        color_texture_path: Path | None = None,
        normal_texture_path: Path | None = None,
        displacement_texture_path: Path | None = None,
        albedo_tile_root: Path | None = None,
        normal_tile_root: Path | None = None,
        tile_settings: MoonTileSettings | None = None,
        tile_manager: MoonTiledTextureManager | None = None,
        lod_widths: Sequence[int] = DEFAULT_LOD_WIDTHS,
        state_sample_capacity: int = 512,
        executor: ThreadPoolExecutor | None = None,
    ) -> None:

        resolved_root = Path(project_root) if project_root is not None else Path(__file__).resolve().parents[2]
        self.project_root = resolved_root
        self.color_texture_path = Path(color_texture_path) if color_texture_path is not None else resolved_root / self.DEFAULT_COLOR_TEXTURE
        self.normal_texture_path = Path(normal_texture_path) if normal_texture_path is not None else None
        self.displacement_texture_path = Path(displacement_texture_path) if displacement_texture_path is not None else resolved_root / self.DEFAULT_DISPLACEMENT_TEXTURE
        self.lod_widths = tuple(sorted({max(16, int(width)) for width in lod_widths}))
        self._state_sample_capacity = max(16, int(state_sample_capacity))
        self._state_samples: OrderedDict[tuple[object, ...], MoonState] = OrderedDict()
        self._texture_lods: OrderedDict[int, MoonTextureLod] = OrderedDict()
        self._normal_texture_lods: OrderedDict[int, MoonTextureLod] = OrderedDict()
        self._texture_lod_future: Future[tuple[MoonTextureLod, ...]] | None = None
        self._normal_texture_lod_future: Future[tuple[MoonTextureLod, ...]] | None = None
        self._texture_lod_error: str = ""
        self._normal_texture_lod_error: str = ""
        self._texture_lod_load_failed = False
        self._normal_texture_lod_load_failed = False
        self._executor = executor or ThreadPoolExecutor(max_workers=1, thread_name_prefix="moon-textures")
        self._owns_executor = executor is None
        self.tile_manager = tile_manager or MoonTiledTextureManager(
            resolved_root,
            executor=self._executor,
            albedo_tile_root=albedo_tile_root,
            normal_tile_root=normal_tile_root,
            settings=tile_settings,
            active_global_albedo_source=self.color_texture_path,
        )

    def shutdown(self) -> None:

        if self._owns_executor:
            self._executor.shutdown(wait=False, cancel_futures=True)

    def get_state_sample(
        self,
        key: tuple[object, ...],
        resolver: Callable[[], MoonState],
    ) -> MoonState:

        cached_state = self._state_samples.get(key)
        if cached_state is not None:
            self._state_samples.move_to_end(key)
            return cached_state

        state = resolver()
        self._state_samples[key] = state
        self._state_samples.move_to_end(key)
        while len(self._state_samples) > self._state_sample_capacity:
            self._state_samples.popitem(last=False)
        return state

    @property
    def state_sample_count(self) -> int:

        return len(self._state_samples)

    @property
    def texture_lod_error(self) -> str:

        self.poll_texture_lods()
        return self._texture_lod_error

    @property
    def normal_texture_lod_error(self) -> str:

        self.poll_normal_texture_lods()
        return self._normal_texture_lod_error

    def request_texture_lods(self) -> bool:

        self.poll_texture_lods()
        if self._texture_lods:
            return False
        if self._texture_lod_load_failed:
            return False
        if self._texture_lod_future is not None and not self._texture_lod_future.done():
            return False
        self._texture_lod_error = ""
        self._texture_lod_future = self._executor.submit(
            self._build_texture_lods,
            self.color_texture_path,
            self.lod_widths,
        )
        return True

    def poll_texture_lods(self) -> None:

        future = self._texture_lod_future
        if future is None or not future.done():
            return

        self._texture_lod_future = None
        try:
            lods = future.result()
        except Exception as exc:
            self._texture_lod_error = f"{type(exc).__name__}: {exc}"
            self._texture_lod_load_failed = True
            return

        if not lods:
            self._texture_lod_error = "Moon albedo texture unavailable"
            self._texture_lod_load_failed = True
            return

        for lod in lods:
            self._texture_lods[int(lod.width)] = lod
            self._texture_lods.move_to_end(int(lod.width))

    def has_pending_texture_work(self) -> bool:

        future = self._texture_lod_future
        return future is not None and not future.done()

    def request_normal_texture_lods(self) -> bool:

        self.poll_normal_texture_lods()
        if self._normal_texture_lods:
            return False
        if self._normal_texture_lod_load_failed:
            return False
        if self._normal_texture_lod_future is not None and not self._normal_texture_lod_future.done():
            return False
        source_path, source_kind = self._normal_texture_source()
        if source_path is None:
            self._normal_texture_lod_error = "Moon normal source unavailable"
            self._normal_texture_lod_load_failed = True
            return False
        self._normal_texture_lod_error = ""
        self._normal_texture_lod_future = self._executor.submit(
            self._build_normal_texture_lods,
            source_path,
            self.lod_widths,
            source_kind,
        )
        return True

    def poll_normal_texture_lods(self) -> None:

        future = self._normal_texture_lod_future
        if future is None or not future.done():
            return

        self._normal_texture_lod_future = None
        try:
            lods = future.result()
        except Exception as exc:
            self._normal_texture_lod_error = f"{type(exc).__name__}: {exc}"
            self._normal_texture_lod_load_failed = True
            return

        if not lods:
            self._normal_texture_lod_error = "Moon normal texture unavailable"
            self._normal_texture_lod_load_failed = True
            return

        for lod in lods:
            self._normal_texture_lods[int(lod.width)] = lod
            self._normal_texture_lods.move_to_end(int(lod.width))

    def has_pending_normal_texture_work(self) -> bool:

        future = self._normal_texture_lod_future
        return future is not None and not future.done()

    def prepare_tiled_textures(
        self,
        draw_state: MoonDrawState,
        moon_state: MoonState,
        *,
        normal_maps_active: bool = False,
        global_albedo_lod: MoonTextureLod | None = None,
        global_normal_lod: MoonTextureLod | None = None,
    ) -> MoonTileDiagnostics:

        return self.tile_manager.prepare_tiles(
            draw_state,
            moon_state,
            normal_maps_active=normal_maps_active,
            global_albedo_lod=global_albedo_lod,
            global_normal_lod=global_normal_lod,
        )

    def moon_tile_shader_bindings(
        self,
        *,
        normal_maps_active: bool = False,
        sampler_limit: int | None = None,
    ) -> tuple[MoonTileShaderBinding, ...]:

        return self.tile_manager.shader_bindings(normal_maps_active=normal_maps_active, sampler_limit=sampler_limit)

    def moon_polar_cap_binding(self, *, normal_maps_active: bool = False) -> MoonPolarCapBinding | None:

        return self.tile_manager.polar_cap_binding(normal_maps_active=normal_maps_active)

    def has_pending_tile_work(self) -> bool:

        return self.tile_manager.has_pending_tile_work()

    @property
    def moon_tile_diagnostics(self) -> MoonTileDiagnostics:

        return self.tile_manager.last_diagnostics

    def texture_lod_for_diameter_px(self, diameter_px: float) -> int:

        diameter = max(1.0, float(diameter_px))
        if diameter <= 48.0:
            target_width = 512
        elif diameter <= 96.0:
            target_width = 1024
        elif diameter <= 192.0:
            target_width = 2048
        elif diameter <= 384.0:
            target_width = 4096
        else:
            target_width = max(self.lod_widths) if self.lod_widths else 8192
        available_widths = self.lod_widths or self.DEFAULT_LOD_WIDTHS
        for width in available_widths:
            if width >= target_width:
                return int(width)
        return int(available_widths[-1])

    def best_available_texture_lod(self, diameter_px: float) -> MoonTextureLod | None:

        self.poll_texture_lods()
        if not self._texture_lods:
            return None
        target_width = self.texture_lod_for_diameter_px(diameter_px)
        lower_or_equal = [width for width in self._texture_lods if width <= target_width]
        selected_width = max(lower_or_equal) if lower_or_equal else min(self._texture_lods)
        lod = self._texture_lods[selected_width]
        self._texture_lods.move_to_end(selected_width)
        return lod

    def best_available_normal_texture_lod(self, diameter_px: float) -> MoonTextureLod | None:

        self.poll_normal_texture_lods()
        if not self._normal_texture_lods:
            return None
        target_width = self.texture_lod_for_diameter_px(diameter_px)
        lower_or_equal = [width for width in self._normal_texture_lods if width <= target_width]
        selected_width = max(lower_or_equal) if lower_or_equal else min(self._normal_texture_lods)
        lod = self._normal_texture_lods[selected_width]
        self._normal_texture_lods.move_to_end(selected_width)
        return lod

    def _normal_texture_source(self) -> tuple[Path | None, str]:

        if self.normal_texture_path is not None and self.normal_texture_path.is_file():
            return self.normal_texture_path, "normal"
        if self.displacement_texture_path.is_file():
            return self.displacement_texture_path, "height"
        return None, "missing"

    @classmethod
    def _build_texture_lods(cls, color_texture_path: Path, lod_widths: Sequence[int]) -> tuple[MoonTextureLod, ...]:

        source_path = Path(color_texture_path)
        if not source_path.is_file():
            return ()

        source_image = QImage(str(source_path))
        if source_image.isNull():
            return ()

        source_image = source_image.convertToFormat(QImage.Format.Format_RGB888)
        source_width = max(1, int(source_image.width()))
        source_height = max(1, int(source_image.height()))
        stat = source_path.stat()
        lods: list[MoonTextureLod] = []
        for requested_width in sorted({max(16, int(width)) for width in lod_widths}):
            lod_width = min(source_width, requested_width)
            lod_height = max(1, int(round(source_height * (float(lod_width) / float(source_width)))))
            if lod_width == source_width and lod_height == source_height:
                lod_image = QImage(source_image)
            else:
                lod_image = source_image.scaled(
                    lod_width,
                    lod_height,
                    Qt.AspectRatioMode.IgnoreAspectRatio,
                    Qt.TransformationMode.SmoothTransformation,
                )
            cache_key = (
                "moon-albedo-lod",
                str(source_path),
                int(stat.st_mtime_ns),
                int(stat.st_size),
                int(lod_image.width()),
                int(lod_image.height()),
            )
            lods.append(
                MoonTextureLod(
                    label=f"{int(lod_image.width())}w",
                    width=int(lod_image.width()),
                    height=int(lod_image.height()),
                    image=lod_image,
                    cache_key=cache_key,
                )
            )
        return tuple(lods)

    @classmethod
    def _build_normal_texture_lods(cls, source_texture_path: Path, lod_widths: Sequence[int], source_kind: str) -> tuple[MoonTextureLod, ...]:

        source_path = Path(source_texture_path)
        if not source_path.is_file():
            return ()

        source_image = QImage(str(source_path))
        if source_image.isNull():
            return ()

        stat = source_path.stat()
        source_width = max(1, int(source_image.width()))
        source_height = max(1, int(source_image.height()))
        lods: list[MoonTextureLod] = []
        for requested_width in sorted({max(16, int(width)) for width in lod_widths}):
            lod_width = min(source_width, requested_width)
            lod_height = max(1, int(round(source_height * (float(lod_width) / float(source_width)))))
            scaled_image = source_image.scaled(
                lod_width,
                lod_height,
                Qt.AspectRatioMode.IgnoreAspectRatio,
                Qt.TransformationMode.SmoothTransformation,
            )
            if source_kind == "normal":
                normal_image = scaled_image.convertToFormat(QImage.Format.Format_RGB888)
            else:
                normal_image = cls._normal_image_from_height_image(scaled_image)
            cache_key = (
                "moon-normal-lod",
                str(source_path),
                str(source_kind),
                int(stat.st_mtime_ns),
                int(stat.st_size),
                int(normal_image.width()),
                int(normal_image.height()),
            )
            lods.append(
                MoonTextureLod(
                    label=f"normal-{int(normal_image.width())}w",
                    width=int(normal_image.width()),
                    height=int(normal_image.height()),
                    image=normal_image,
                    cache_key=cache_key,
                )
            )
        return tuple(lods)

    @staticmethod
    def _normal_image_from_height_image(
        height_image: QImage,
        *,
        smoothing_passes: int = 1,
        polar_smoothing_passes: int = 0,
        polar_derivative_fade_start_lat_deg: float | None = None,
        polar_derivative_fade_end_lat_deg: float | None = None,
    ) -> QImage:

        grayscale = height_image.convertToFormat(QImage.Format.Format_Grayscale8)
        width = max(1, int(grayscale.width()))
        height = max(1, int(grayscale.height()))
        bytes_per_line = int(grayscale.bytesPerLine())
        height_values = np.frombuffer(grayscale.bits(), dtype=np.uint8, count=bytes_per_line * height).reshape((height, bytes_per_line))[:, :width].astype(np.float32) / 255.0
        def smoothed_height(values: np.ndarray) -> np.ndarray:

            padded_height = np.pad(values, ((1, 1), (1, 1)), mode="wrap")
            padded_height[0, :] = padded_height[1, :]
            padded_height[-1, :] = padded_height[-2, :]
            return (
                padded_height[1:-1, 1:-1] * 4.0
                + (padded_height[1:-1, :-2] + padded_height[1:-1, 2:] + padded_height[:-2, 1:-1] + padded_height[2:, 1:-1]) * 2.0
                + padded_height[:-2, :-2]
                + padded_height[:-2, 2:]
                + padded_height[2:, :-2]
                + padded_height[2:, 2:]
            ) / 16.0

        smoothed = height_values
        for _ in range(max(0, int(smoothing_passes))):
            smoothed = smoothed_height(smoothed)
        latitude_degrees = np.abs(np.linspace(90.0, -90.0, height, dtype=np.float32)).reshape((height, 1))
        polar_fade: np.ndarray | None = None
        if polar_derivative_fade_start_lat_deg is not None and polar_derivative_fade_end_lat_deg is not None:
            fade_start = max(0.0, min(90.0, float(polar_derivative_fade_start_lat_deg)))
            fade_end = max(fade_start + 1.0e-6, min(90.0, float(polar_derivative_fade_end_lat_deg)))
            fade_t = np.clip((latitude_degrees - fade_start) / (fade_end - fade_start), 0.0, 1.0)
            polar_fade = fade_t * fade_t * (3.0 - 2.0 * fade_t)
        if int(polar_smoothing_passes) > 0 and polar_fade is not None:
            polar_smoothed = smoothed
            for _ in range(max(0, int(polar_smoothing_passes))):
                polar_smoothed = smoothed_height(polar_smoothed)
            smoothed = smoothed * (1.0 - polar_fade) + polar_smoothed * polar_fade
        gradient_x = (np.roll(smoothed, -1, axis=1) - np.roll(smoothed, 1, axis=1)) * 0.5
        padded = np.pad(smoothed, ((1, 1), (0, 0)), mode="edge")
        gradient_y = (padded[2:, :] - padded[:-2, :]) * 0.5
        latitude_weight = np.sin(np.linspace(0.0, math.pi, height, dtype=np.float32)).reshape((height, 1))
        pole_damping = np.clip(latitude_weight, 0.28, 1.0)
        gradient_x *= pole_damping
        gradient_y *= pole_damping
        if polar_fade is not None:
            gradient_x *= 1.0 - polar_fade
            gradient_y *= 1.0 - polar_fade
        strength = 2.15
        nx = -gradient_x * strength
        ny = -gradient_y * strength
        nz = np.ones_like(nx)
        normal_length = np.sqrt(nx * nx + ny * ny + nz * nz)
        normal_rgb = np.dstack(
            (
                (nx / normal_length * 0.5 + 0.5) * 255.0,
                (ny / normal_length * 0.5 + 0.5) * 255.0,
                (nz / normal_length * 0.5 + 0.5) * 255.0,
            )
        ).clip(0, 255).astype(np.uint8)
        normal_rgb = np.ascontiguousarray(normal_rgb)
        return QImage(normal_rgb.data, width, height, width * 3, QImage.Format.Format_RGB888).copy()

    @staticmethod
    def _normal_image_from_planar_height_image(
        height_image: QImage,
        *,
        smoothing_passes: int = 2,
    ) -> QImage:

        grayscale = height_image.convertToFormat(QImage.Format.Format_Grayscale8)
        width = max(1, int(grayscale.width()))
        height = max(1, int(grayscale.height()))
        bytes_per_line = int(grayscale.bytesPerLine())
        values = np.frombuffer(grayscale.bits(), dtype=np.uint8, count=bytes_per_line * height).reshape((height, bytes_per_line))[:, :width].astype(np.float32) / 255.0
        for _ in range(max(0, int(smoothing_passes))):
            padded = np.pad(values, ((1, 1), (1, 1)), mode="edge")
            values = (
                padded[1:-1, 1:-1] * 4.0
                + (padded[1:-1, :-2] + padded[1:-1, 2:] + padded[:-2, 1:-1] + padded[2:, 1:-1]) * 2.0
                + padded[:-2, :-2]
                + padded[:-2, 2:]
                + padded[2:, :-2]
                + padded[2:, 2:]
            ) / 16.0
        padded = np.pad(values, ((1, 1), (1, 1)), mode="edge")
        gradient_x = (padded[1:-1, 2:] - padded[1:-1, :-2]) * 0.5
        gradient_y = (padded[2:, 1:-1] - padded[:-2, 1:-1]) * 0.5
        strength = 1.35
        nx = -gradient_x * strength
        ny = -gradient_y * strength
        nz = np.ones_like(nx)
        normal_length = np.sqrt(nx * nx + ny * ny + nz * nz)
        normal_rgb = np.dstack(
            (
                (nx / normal_length * 0.5 + 0.5) * 255.0,
                (ny / normal_length * 0.5 + 0.5) * 255.0,
                (nz / normal_length * 0.5 + 0.5) * 255.0,
            )
        ).clip(0, 255).astype(np.uint8)
        normal_rgb = np.ascontiguousarray(normal_rgb)
        return QImage(normal_rgb.data, width, height, width * 3, QImage.Format.Format_RGB888).copy()


class MoonEphemeris:

    MOON_RADIUS_KM = 1737.4

    def __init__(
        self,
        cache: MoonCache | None = None,
        *,
        sample_interval_seconds: int = 300,
        precise_state_resolver: Callable[[datetime, float, float, float], MoonState] | None = None,
        orientation_provider: LunarOrientationProvider | None = None,
    ) -> None:

        self._cache = cache or MoonCache()
        self._sample_interval_seconds = max(30, int(sample_interval_seconds))
        if orientation_provider is None:
            self._orientation_provider_selection = select_lunar_orientation_provider(project_root=self._cache.project_root)
            self._orientation_provider = self._orientation_provider_selection.provider
        else:
            self._orientation_provider = orientation_provider
            self._orientation_provider_selection = LunarOrientationProviderSelection(
                provider=orientation_provider,
                diagnostics=LunarOrientationProviderDiagnostics(
                    requested_mode="custom",
                    active_provider=type(orientation_provider).__name__,
                    spice_available=isinstance(orientation_provider, LunarOrientationSpiceProvider),
                    spice_kernel_paths=(),
                    loaded_kernel_count=getattr(orientation_provider, "loaded_kernel_count", 0),
                    diagnostic="Custom lunar orientation provider supplied.",
                ),
            )
        self._precise_state_resolver = precise_state_resolver or (
            lambda observation_time, latitude_deg, longitude_deg, elevation_m: self._compute_precise_state(
                observation_time,
                latitude_deg,
                longitude_deg,
                elevation_m,
                orientation_provider=self._orientation_provider,
            )
        )

    @property
    def orientation_provider_diagnostics(self) -> LunarOrientationProviderDiagnostics:

        return self._orientation_provider_selection.diagnostics

    def state_at(
        self,
        observation_time_utc: datetime,
        latitude_deg: float,
        longitude_deg: float,
        elevation_m: float,
    ) -> MoonState:

        benchmark_recorder = get_benchmark_recorder() if BENCHMARK_ENABLED else None

        benchmark_token = benchmark_recorder.start_section("moon.ephemeris.state_at") if benchmark_recorder is not None else None

        resolved_time = self._normalize_time(observation_time_utc)
        epoch_seconds = float(resolved_time.timestamp())
        lower_epoch = int(math.floor(epoch_seconds / float(self._sample_interval_seconds))) * self._sample_interval_seconds
        upper_epoch = lower_epoch + self._sample_interval_seconds
        lower_time = datetime.fromtimestamp(lower_epoch, UTC)
        upper_time = datetime.fromtimestamp(upper_epoch, UTC)
        observer_key = self._observer_key(latitude_deg, longitude_deg, elevation_m)
        lower_state = self._cache.get_state_sample(
            ("moon-state", lower_epoch, observer_key),
            lambda: self._precise_state_resolver(lower_time, latitude_deg, longitude_deg, elevation_m),
        )
        if abs(epoch_seconds - float(lower_epoch)) <= 1.0e-9:
            if benchmark_recorder is not None:
                benchmark_recorder.stop_section(benchmark_token, metadata={"interpolated": False})
            return lower_state
        upper_state = self._cache.get_state_sample(
            ("moon-state", upper_epoch, observer_key),
            lambda: self._precise_state_resolver(upper_time, latitude_deg, longitude_deg, elevation_m),
        )
        factor = (epoch_seconds - float(lower_epoch)) / max(1.0, float(self._sample_interval_seconds))
        state = self._interpolate_states(lower_state, upper_state, factor, resolved_time)
        if benchmark_recorder is not None:
            benchmark_recorder.stop_section(benchmark_token, metadata={"interpolated": True})
        return state

    def precise_state_at(
        self,
        observation_time_utc: datetime,
        latitude_deg: float,
        longitude_deg: float,
        elevation_m: float,
    ) -> MoonState:

        return self._precise_state_resolver(
            self._normalize_time(observation_time_utc),
            float(latitude_deg),
            float(longitude_deg),
            float(elevation_m),
        )

    @classmethod
    def _compute_precise_state(
        cls,
        observation_time_utc: datetime,
        latitude_deg: float,
        longitude_deg: float,
        elevation_m: float,
        *,
        orientation_provider: LunarOrientationProvider | None = None,
    ) -> MoonState:

        resolved_time = cls._normalize_time(observation_time_utc)
        location = EarthLocation(
            lat=float(latitude_deg) * u.deg,
            lon=float(longitude_deg) * u.deg,
            height=float(elevation_m) * u.m,
        )
        astropy_time = Time(resolved_time, scale="utc")
        with iers.conf.set_temp("auto_max_age", None):
            moon = get_body("moon", astropy_time, location)
            sun = get_body("sun", astropy_time, location)
            altaz_frame = AltAz(obstime=astropy_time, location=location)
            moon_altaz = moon.transform_to(altaz_frame)
        moon_vector = np.asarray(moon.cartesian.xyz.to_value(u.km), dtype=float)
        sun_vector = np.asarray(sun.cartesian.xyz.to_value(u.km), dtype=float)
        moon_to_sun = sun_vector - moon_vector
        moon_to_observer = -moon_vector
        moon_to_sun_direction = cls._normalize_vector(tuple(float(value) for value in moon_to_sun))
        moon_direction = cls._normalize_vector(tuple(float(value) for value in moon_vector))
        phase_angle_rad = cls._vector_angle(
            moon_to_sun_direction,
            cls._normalize_vector(tuple(float(value) for value in moon_to_observer)),
        )
        distance_km = max(1.0, float(moon.distance.to_value(u.km)))
        angular_diameter_deg = math.degrees(2.0 * math.atan2(cls.MOON_RADIUS_KM, distance_km))
        illuminated_fraction = (1.0 + math.cos(phase_angle_rad)) * 0.5
        orientation = (orientation_provider or LunarOrientationApprox()).compute(
            resolved_time,
            ra_deg=float(moon.ra.deg) % 360.0,
            dec_deg=max(-90.0, min(90.0, float(moon.dec.deg))),
            observer_latitude_deg=float(latitude_deg),
            observer_longitude_deg=float(longitude_deg),
            moon_direction_equatorial=moon_direction,
            moon_to_sun_direction_equatorial=moon_to_sun_direction,
        )
        return MoonState(
            observation_time_utc=resolved_time,
            observer_latitude_deg=float(latitude_deg),
            observer_longitude_deg=float(longitude_deg),
            observer_elevation_m=float(elevation_m),
            ra_deg=float(moon.ra.deg) % 360.0,
            dec_deg=max(-90.0, min(90.0, float(moon.dec.deg))),
            altitude_deg=max(-90.0, min(90.0, float(moon_altaz.alt.deg))),
            azimuth_deg=float(moon_altaz.az.deg) % 360.0,
            distance_km=distance_km,
            angular_diameter_deg=angular_diameter_deg,
            phase_angle_deg=math.degrees(phase_angle_rad),
            illuminated_fraction=max(0.0, min(1.0, float(illuminated_fraction))),
            moon_direction_equatorial=moon_direction,
            moon_to_sun_direction_equatorial=moon_to_sun_direction,
            libration_longitude_rad=orientation.libration_longitude_rad,
            libration_latitude_rad=orientation.libration_latitude_rad,
            lunar_north_position_angle_rad=orientation.lunar_north_position_angle_rad,
            parallactic_angle_rad=orientation.parallactic_angle_rad,
            moon_to_equatorial_matrix=orientation.moon_to_equatorial_matrix,
            equatorial_to_moon_matrix=orientation.equatorial_to_moon_matrix,
            view_to_moon_matrix=orientation.view_to_moon_matrix,
            moon_to_view_matrix=orientation.moon_to_view_matrix,
            sun_dir_moon_local=orientation.sun_dir_moon_local,
            observer_dir_moon_local=orientation.observer_dir_moon_local,
            earth_dir_moon_local=orientation.earth_dir_moon_local,
        )

    @classmethod
    def _interpolate_states(
        cls,
        lower: MoonState,
        upper: MoonState,
        factor: float,
        observation_time_utc: datetime,
    ) -> MoonState:

        t = max(0.0, min(1.0, float(factor)))
        moon_direction = cls._normalize_vector(cls._lerp_vector(lower.moon_direction_equatorial, upper.moon_direction_equatorial, t))
        moon_to_sun_direction = cls._normalize_vector(cls._lerp_vector(lower.moon_to_sun_direction_equatorial, upper.moon_to_sun_direction_equatorial, t))
        equatorial_to_moon = cls._slerp_matrix(lower.equatorial_to_moon_matrix, upper.equatorial_to_moon_matrix, t)
        moon_to_equatorial = cls._matrix_transpose(equatorial_to_moon)
        view_to_moon = cls._slerp_matrix(lower.view_to_moon_matrix, upper.view_to_moon_matrix, t)
        moon_to_view = cls._matrix_transpose(view_to_moon)
        return MoonState(
            observation_time_utc=observation_time_utc,
            observer_latitude_deg=lower.observer_latitude_deg,
            observer_longitude_deg=lower.observer_longitude_deg,
            observer_elevation_m=lower.observer_elevation_m,
            ra_deg=cls._interpolate_angle_deg(lower.ra_deg, upper.ra_deg, t),
            dec_deg=lower.dec_deg + (upper.dec_deg - lower.dec_deg) * t,
            altitude_deg=lower.altitude_deg + (upper.altitude_deg - lower.altitude_deg) * t,
            azimuth_deg=cls._interpolate_angle_deg(lower.azimuth_deg, upper.azimuth_deg, t),
            distance_km=lower.distance_km + (upper.distance_km - lower.distance_km) * t,
            angular_diameter_deg=lower.angular_diameter_deg + (upper.angular_diameter_deg - lower.angular_diameter_deg) * t,
            phase_angle_deg=lower.phase_angle_deg + (upper.phase_angle_deg - lower.phase_angle_deg) * t,
            illuminated_fraction=lower.illuminated_fraction + (upper.illuminated_fraction - lower.illuminated_fraction) * t,
            moon_direction_equatorial=moon_direction,
            moon_to_sun_direction_equatorial=moon_to_sun_direction,
            libration_longitude_rad=cls._interpolate_angle_rad(lower.libration_longitude_rad, upper.libration_longitude_rad, t),
            libration_latitude_rad=lower.libration_latitude_rad + (upper.libration_latitude_rad - lower.libration_latitude_rad) * t,
            lunar_north_position_angle_rad=cls._interpolate_angle_rad(lower.lunar_north_position_angle_rad, upper.lunar_north_position_angle_rad, t),
            parallactic_angle_rad=cls._interpolate_angle_rad(lower.parallactic_angle_rad, upper.parallactic_angle_rad, t),
            moon_to_equatorial_matrix=moon_to_equatorial,
            equatorial_to_moon_matrix=equatorial_to_moon,
            view_to_moon_matrix=view_to_moon,
            moon_to_view_matrix=moon_to_view,
            sun_dir_moon_local=cls._normalize_vector(cls._lerp_vector(lower.sun_dir_moon_local, upper.sun_dir_moon_local, t)),
            observer_dir_moon_local=cls._normalize_vector(cls._lerp_vector(lower.observer_dir_moon_local, upper.observer_dir_moon_local, t)),
            earth_dir_moon_local=cls._normalize_vector(cls._lerp_vector(lower.earth_dir_moon_local, upper.earth_dir_moon_local, t)),
        )

    @staticmethod
    def _normalize_time(value: datetime) -> datetime:

        return value.replace(tzinfo=UTC) if value.tzinfo is None else value.astimezone(UTC)

    @staticmethod
    def _observer_key(latitude_deg: float, longitude_deg: float, elevation_m: float) -> tuple[float, float, float]:

        return (round(float(latitude_deg), 5), round(float(longitude_deg), 5), round(float(elevation_m), 1))

    @staticmethod
    def _interpolate_angle_deg(start_deg: float, end_deg: float, factor: float) -> float:

        delta = ((float(end_deg) - float(start_deg) + 180.0) % 360.0) - 180.0
        return (float(start_deg) + delta * max(0.0, min(1.0, float(factor)))) % 360.0

    @staticmethod
    def _angle_distance_deg(lhs_deg: float, rhs_deg: float) -> float:

        return abs(((float(lhs_deg) - float(rhs_deg) + 180.0) % 360.0) - 180.0)

    @staticmethod
    def _interpolate_angle_rad(start_rad: float, end_rad: float, factor: float) -> float:

        delta = ((float(end_rad) - float(start_rad) + math.pi) % (2.0 * math.pi)) - math.pi
        return float(start_rad) + delta * max(0.0, min(1.0, float(factor)))

    @staticmethod
    def _lerp_vector(lhs: Vector3, rhs: Vector3, factor: float) -> Vector3:

        return (
            lhs[0] + (rhs[0] - lhs[0]) * factor,
            lhs[1] + (rhs[1] - lhs[1]) * factor,
            lhs[2] + (rhs[2] - lhs[2]) * factor,
        )

    @classmethod
    def _vector_angle(cls, lhs: Vector3, rhs: Vector3) -> float:

        return math.acos(max(-1.0, min(1.0, cls._dot(cls._normalize_vector(lhs), cls._normalize_vector(rhs)))))

    @staticmethod
    def _dot(lhs: Vector3, rhs: Vector3) -> float:

        return lhs[0] * rhs[0] + lhs[1] * rhs[1] + lhs[2] * rhs[2]

    @staticmethod
    def _normalize_vector(vector: Vector3) -> Vector3:

        length = math.sqrt(vector[0] * vector[0] + vector[1] * vector[1] + vector[2] * vector[2])
        if length <= 1.0e-12:
            return (0.0, 0.0, 1.0)
        return (vector[0] / length, vector[1] / length, vector[2] / length)

    @staticmethod
    def _matrix_transpose(matrix: Matrix3) -> Matrix3:

        return (
            (matrix[0][0], matrix[1][0], matrix[2][0]),
            (matrix[0][1], matrix[1][1], matrix[2][1]),
            (matrix[0][2], matrix[1][2], matrix[2][2]),
        )

    @classmethod
    def _slerp_matrix(cls, lower: Matrix3, upper: Matrix3, factor: float) -> Matrix3:

        lower_quaternion = cls._matrix_to_quaternion(lower)
        upper_quaternion = cls._matrix_to_quaternion(upper)
        return cls._quaternion_to_matrix(cls._slerp_quaternion(lower_quaternion, upper_quaternion, factor))

    @staticmethod
    def _matrix_to_quaternion(matrix: Matrix3) -> tuple[float, float, float, float]:

        trace = matrix[0][0] + matrix[1][1] + matrix[2][2]
        if trace > 0.0:
            scale = math.sqrt(trace + 1.0) * 2.0
            return (
                0.25 * scale,
                (matrix[2][1] - matrix[1][2]) / scale,
                (matrix[0][2] - matrix[2][0]) / scale,
                (matrix[1][0] - matrix[0][1]) / scale,
            )
        if matrix[0][0] > matrix[1][1] and matrix[0][0] > matrix[2][2]:
            scale = math.sqrt(max(1.0e-18, 1.0 + matrix[0][0] - matrix[1][1] - matrix[2][2])) * 2.0
            return (
                (matrix[2][1] - matrix[1][2]) / scale,
                0.25 * scale,
                (matrix[0][1] + matrix[1][0]) / scale,
                (matrix[0][2] + matrix[2][0]) / scale,
            )
        if matrix[1][1] > matrix[2][2]:
            scale = math.sqrt(max(1.0e-18, 1.0 + matrix[1][1] - matrix[0][0] - matrix[2][2])) * 2.0
            return (
                (matrix[0][2] - matrix[2][0]) / scale,
                (matrix[0][1] + matrix[1][0]) / scale,
                0.25 * scale,
                (matrix[1][2] + matrix[2][1]) / scale,
            )
        scale = math.sqrt(max(1.0e-18, 1.0 + matrix[2][2] - matrix[0][0] - matrix[1][1])) * 2.0
        return (
            (matrix[1][0] - matrix[0][1]) / scale,
            (matrix[0][2] + matrix[2][0]) / scale,
            (matrix[1][2] + matrix[2][1]) / scale,
            0.25 * scale,
        )

    @staticmethod
    def _slerp_quaternion(
        lower: tuple[float, float, float, float],
        upper: tuple[float, float, float, float],
        factor: float,
    ) -> tuple[float, float, float, float]:

        t = max(0.0, min(1.0, float(factor)))
        dot = lower[0] * upper[0] + lower[1] * upper[1] + lower[2] * upper[2] + lower[3] * upper[3]
        if dot < 0.0:
            upper = (-upper[0], -upper[1], -upper[2], -upper[3])
            dot = -dot
        if dot > 0.9995:
            result = (
                lower[0] + (upper[0] - lower[0]) * t,
                lower[1] + (upper[1] - lower[1]) * t,
                lower[2] + (upper[2] - lower[2]) * t,
                lower[3] + (upper[3] - lower[3]) * t,
            )
            length = math.sqrt(result[0] * result[0] + result[1] * result[1] + result[2] * result[2] + result[3] * result[3])
            return (result[0] / length, result[1] / length, result[2] / length, result[3] / length)
        theta_0 = math.acos(max(-1.0, min(1.0, dot)))
        theta = theta_0 * t
        sin_theta = math.sin(theta)
        sin_theta_0 = math.sin(theta_0)
        scale_lower = math.cos(theta) - dot * sin_theta / sin_theta_0
        scale_upper = sin_theta / sin_theta_0
        return (
            lower[0] * scale_lower + upper[0] * scale_upper,
            lower[1] * scale_lower + upper[1] * scale_upper,
            lower[2] * scale_lower + upper[2] * scale_upper,
            lower[3] * scale_lower + upper[3] * scale_upper,
        )

    @staticmethod
    def _quaternion_to_matrix(quaternion: tuple[float, float, float, float]) -> Matrix3:

        w, x, y, z = quaternion
        length = math.sqrt(w * w + x * x + y * y + z * z)
        if length <= 1.0e-12:
            return IDENTITY_MATRIX_3
        w /= length
        x /= length
        y /= length
        z /= length
        return (
            (1.0 - 2.0 * (y * y + z * z), 2.0 * (x * y - z * w), 2.0 * (x * z + y * w)),
            (2.0 * (x * y + z * w), 1.0 - 2.0 * (x * x + z * z), 2.0 * (y * z - x * w)),
            (2.0 * (x * z - y * w), 2.0 * (y * z + x * w), 1.0 - 2.0 * (x * x + y * y)),
        )


@dataclass(frozen=True, slots=True)
class MoonEphemerisRuntimeDiagnostics:

    simulation_time_utc: datetime | None = None
    bracket_start_utc: datetime | None = None
    bracket_end_utc: datetime | None = None
    interpolation_alpha: float = 0.0
    update_interval_seconds: int = 300
    cache_status: str = "empty"
    prefetch_status: str = "idle"
    pending_sample_count: int = 0
    estimated_interpolation_error_px: float | None = None
    invalidated_due_to_scrub: bool = False


class MoonAsyncEphemerisSampler:
    """Schedules accurate Moon samples off the paint path and interpolates ready brackets."""

    def __init__(
        self,
        ephemeris: MoonEphemeris,
        *,
        executor: ThreadPoolExecutor | None = None,
    ) -> None:

        self._ephemeris = ephemeris
        self._executor = executor or ThreadPoolExecutor(max_workers=2, thread_name_prefix="moon-ephemeris")
        self._owns_executor = executor is None
        self._states: OrderedDict[tuple[tuple[float, float, float], int, int], MoonState] = OrderedDict()
        self._futures: dict[tuple[tuple[float, float, float], int, int], Future[MoonState]] = {}
        self._last_observation_time_utc: datetime | None = None
        self._last_observer_key: tuple[float, float, float] | None = None
        self._last_state: MoonState | None = None
        self._last_error: Exception | None = None
        self.diagnostics = MoonEphemerisRuntimeDiagnostics()

    @staticmethod
    def update_interval_seconds(moon_diameter_px: float) -> int:

        diameter_px = max(0.0, float(moon_diameter_px))
        if diameter_px >= 700.0:
            return 30
        if diameter_px >= 300.0:
            return 60
        if diameter_px >= 100.0:
            return 120
        return 300

    def invalidate(self, *, preserve_last_state: bool = True) -> None:

        for future in self._futures.values():
            future.cancel()
        self._futures.clear()
        self._states.clear()
        self._last_observation_time_utc = None
        self._last_observer_key = None
        self._last_error = None
        if not preserve_last_state:
            self._last_state = None

    def shutdown(self) -> None:

        self.invalidate(preserve_last_state=False)
        if self._owns_executor:
            self._executor.shutdown(wait=False, cancel_futures=True)

    def state_at_nonblocking(
        self,
        observation_time_utc: datetime,
        latitude_deg: float,
        longitude_deg: float,
        elevation_m: float,
        *,
        moon_diameter_px: float = 0.0,
        pixels_per_degree: float = 0.0,
        time_rate: float = 1.0,
    ) -> MoonState | None:

        resolved_time = self._ephemeris._normalize_time(observation_time_utc)
        interval_seconds = self.update_interval_seconds(moon_diameter_px)
        observer_key = self._ephemeris._observer_key(latitude_deg, longitude_deg, elevation_m)
        invalidated_due_to_scrub = False
        if self._last_observer_key is not None and observer_key != self._last_observer_key:
            self.invalidate(preserve_last_state=False)
            invalidated_due_to_scrub = True
        elif self._last_observation_time_utc is not None:
            elapsed_seconds = abs((resolved_time - self._last_observation_time_utc).total_seconds())
            if elapsed_seconds > max(600.0, interval_seconds * 2.0):
                self.invalidate()
                invalidated_due_to_scrub = True

        self._last_observer_key = observer_key
        self._last_observation_time_utc = resolved_time
        self._consume_ready_samples()

        epoch_seconds = float(resolved_time.timestamp())
        lower_epoch = int(math.floor(epoch_seconds / float(interval_seconds))) * interval_seconds
        upper_epoch = lower_epoch + interval_seconds
        lower_key = (observer_key, interval_seconds, lower_epoch)
        upper_key = (observer_key, interval_seconds, upper_epoch)
        prefetch_epochs = [lower_epoch, upper_epoch]
        prefetch_count = 1 if abs(float(time_rate)) < 30.0 else min(4, 1 + int(abs(float(time_rate)) // 120.0))
        direction = 1 if float(time_rate) >= 0.0 else -1
        for offset in range(1, prefetch_count + 1):
            prefetch_epochs.append(upper_epoch + (offset * interval_seconds) if direction >= 0 else lower_epoch - (offset * interval_seconds))
        for sample_epoch in prefetch_epochs:
            self._request_sample(
                (observer_key, interval_seconds, sample_epoch),
                datetime.fromtimestamp(sample_epoch, UTC),
                latitude_deg,
                longitude_deg,
                elevation_m,
            )
        self._consume_ready_samples()

        lower_state = self._states.get(lower_key)
        upper_state = self._states.get(upper_key)
        alpha = max(0.0, min(1.0, (epoch_seconds - float(lower_epoch)) / max(1.0, float(interval_seconds))))
        state: MoonState | None
        cache_status: str
        estimated_error_px: float | None = None
        if lower_state is not None and (upper_state is not None or alpha <= 1.0e-9):
            if upper_state is None:
                state = replace(lower_state, observation_time_utc=resolved_time)
            else:
                state = self._ephemeris._interpolate_states(lower_state, upper_state, alpha, resolved_time)
                angular_span_deg = math.degrees(
                    self._ephemeris._vector_angle(
                        lower_state.moon_direction_equatorial,
                        upper_state.moon_direction_equatorial,
                    )
                )
                estimated_error_px = angular_span_deg * max(0.0, float(pixels_per_degree)) * 0.002
            self._last_state = state
            cache_status = "interpolated"
        elif self._last_state is not None:
            state = replace(self._last_state, observation_time_utc=resolved_time)
            cache_status = "holding_previous"
        else:
            state = None
            cache_status = "pending"
        if self._last_error is not None and state is None:
            cache_status = f"error:{type(self._last_error).__name__}"

        self.diagnostics = MoonEphemerisRuntimeDiagnostics(
            simulation_time_utc=resolved_time,
            bracket_start_utc=datetime.fromtimestamp(lower_epoch, UTC),
            bracket_end_utc=datetime.fromtimestamp(upper_epoch, UTC),
            interpolation_alpha=alpha,
            update_interval_seconds=interval_seconds,
            cache_status=cache_status,
            prefetch_status="pending" if self._futures else "ready",
            pending_sample_count=len(self._futures),
            estimated_interpolation_error_px=estimated_error_px,
            invalidated_due_to_scrub=invalidated_due_to_scrub,
        )
        return state

    def _request_sample(
        self,
        key: tuple[tuple[float, float, float], int, int],
        sample_time_utc: datetime,
        latitude_deg: float,
        longitude_deg: float,
        elevation_m: float,
    ) -> None:

        if key in self._states or key in self._futures:

            return

        self._futures[key] = self._executor.submit(
            self._ephemeris.precise_state_at,
            sample_time_utc,
            float(latitude_deg),
            float(longitude_deg),
            float(elevation_m),
        )

    def _consume_ready_samples(self) -> None:

        for key, future in tuple(self._futures.items()):
            if not future.done():

                continue
            del self._futures[key]
            if future.cancelled():

                continue
            try:
                self._states[key] = future.result()
                self._states.move_to_end(key)
                self._last_error = None
            except Exception as exc:
                self._last_error = exc
        while len(self._states) > 24:
            self._states.popitem(last=False)


class MoonRendererGL:

    MOON_TILE_SAMPLER_LIMIT = 6

    _GL_ACTIVE_TEXTURE = 0x84E0
    _GL_BLEND = 0x0BE2
    _GL_BLEND_DST = 0x0BE0
    _GL_BLEND_SRC = 0x0BE1
    _GL_CURRENT_PROGRAM = 0x8B8D
    _GL_DEPTH_TEST = 0x0B71
    _GL_LINEAR = 0x2601
    _GL_LINEAR_MIPMAP_LINEAR = 0x2703
    _GL_NO_ERROR = 0
    _GL_ONE_MINUS_SRC_ALPHA = 0x0303
    _GL_QUADS = 0x0007
    _GL_SCISSOR_TEST = 0x0C11
    _GL_SRC_ALPHA = 0x0302
    _GL_STENCIL_TEST = 0x0B90
    _GL_TEXTURE0 = 0x84C0
    _GL_TEXTURE1 = 0x84C1
    _GL_TEXTURE_2D = 0x0DE1
    _GL_TEXTURE_BINDING_2D = 0x8069
    _GL_VIEWPORT = 0x0BA2

    _VERTEX_SHADER_SOURCE = """
#version 120

void main() {
    gl_Position = vec4(gl_Vertex.xy, 0.0, 1.0);
}
"""

    _FRAGMENT_SHADER_SOURCE = (
        """
#version 120

uniform vec2 u_center_px;
uniform float u_radius_px;
uniform float u_opacity;
uniform vec3 u_sun_dir_moon;
uniform vec3 u_earth_dir_moon;
uniform vec3 u_view_to_moon_row0;
uniform vec3 u_view_to_moon_row1;
uniform vec3 u_view_to_moon_row2;
uniform sampler2D u_albedo_texture;
uniform sampler2D u_moon_normal_texture;
uniform int u_has_moon_normal_map;
uniform int u_has_global_moon_normal_map;
uniform float u_terrain_normal_strength;
uniform float u_terrain_normal_strength_global;
uniform float u_terrain_normal_strength_terminator;
uniform float u_tiled_normal_strength;
uniform float u_tiled_terminator_normal_strength;
uniform float u_tiled_normal_fade_factor;
uniform float u_tiled_normal_max_slope;
uniform float u_earthshine_strength;
uniform float u_earthshine_floor;
uniform float u_atmosphere_extinction;
uniform vec3 u_atmosphere_tint_rgb;
uniform int u_debug_render_mode;
uniform int u_disable_tone_mapping;
uniform int u_force_tiled_texture_only;
uniform int u_moonTileAlbedoCount;
uniform float u_moonTileEdgeFade;
uniform sampler2D u_moonTileAlbedo0;
uniform sampler2D u_moonTileAlbedo1;
uniform sampler2D u_moonTileAlbedo2;
uniform sampler2D u_moonTileAlbedo3;
uniform sampler2D u_moonTileAlbedo4;
uniform sampler2D u_moonTileAlbedo5;
uniform sampler2D u_moonTileNormal0;
uniform sampler2D u_moonTileNormal1;
uniform sampler2D u_moonTileNormal2;
uniform sampler2D u_moonTileNormal3;
uniform sampler2D u_moonTileNormal4;
uniform sampler2D u_moonTileNormal5;
uniform vec4 u_moonTileBounds0;
uniform vec4 u_moonTileBounds1;
uniform vec4 u_moonTileBounds2;
uniform vec4 u_moonTileBounds3;
uniform vec4 u_moonTileBounds4;
uniform vec4 u_moonTileBounds5;
uniform int u_moonTileNormalEnabled0;
uniform int u_moonTileNormalEnabled1;
uniform int u_moonTileNormalEnabled2;
uniform int u_moonTileNormalEnabled3;
uniform int u_moonTileNormalEnabled4;
uniform int u_moonTileNormalEnabled5;
uniform sampler2D u_moonPolarCapNorthAlbedo;
uniform sampler2D u_moonPolarCapSouthAlbedo;
uniform sampler2D u_moonPolarCapNorthNormal;
uniform sampler2D u_moonPolarCapSouthNormal;
uniform int u_moonPolarCapNorthAlbedoEnabled;
uniform int u_moonPolarCapSouthAlbedoEnabled;
uniform int u_moonPolarCapNorthNormalEnabled;
uniform int u_moonPolarCapSouthNormalEnabled;
uniform float u_polarCapNormalStrength;
uniform float u_polarCapBlendStartLatDeg;
uniform float u_polarCapBlendEndLatDeg;
uniform float u_polarCapBlendCurvePower;
uniform float u_polarCapNormalBlendStartLatDeg;
uniform float u_polarCapNormalBlendEndLatDeg;
uniform float u_polarCapLatitudeCoverageDeg;
uniform float u_polarCapTextureSizePx;
uniform float u_polarCapAlbedoSharpness;
uniform float u_polarCapLocalContrast;
uniform float u_polarCapBoundaryMatchStrength;
uniform float u_polarCapCoreMatchStrength;

const float PI = 3.14159265358979323846;
const float TAU = 6.28318530717958647692;
"""
    + _moon_fragment_visual_constant_source(DEFAULT_MOON_VISUAL_SETTINGS)
    +
    """

bool moonTileContainsUv(vec2 uv, vec4 bounds) {
    bool contains_u = bounds.x <= bounds.z ? (uv.x >= bounds.x && uv.x <= bounds.z) : (uv.x >= bounds.x || uv.x <= bounds.z);
    return contains_u && uv.y >= bounds.y && uv.y <= bounds.w;
}

vec2 moonTileLocalUv(vec2 uv, vec4 bounds) {
    float sample_u = uv.x;
    float span_u = bounds.z - bounds.x;
    if (span_u < 0.0) {
        span_u += 1.0;
        if (sample_u < bounds.x) {
            sample_u += 1.0;
        }
    }
    float span_v = max(bounds.w - bounds.y, 1.0e-6);
    return vec2((sample_u - bounds.x) / max(span_u, 1.0e-6), (uv.y - bounds.y) / span_v);
}

float moonTileEdgeWeight(vec2 local_uv) {
    float edge = min(min(local_uv.x, 1.0 - local_uv.x), min(local_uv.y, 1.0 - local_uv.y));
    return smoothstep(0.0, max(u_moonTileEdgeFade, 1.0e-5), edge);
}

float moonCompressHighlight(float value) {
    if (value <= MOON_TONE_HIGHLIGHT_KNEE) {
        return value;
    }
    float excess = value - MOON_TONE_HIGHLIGHT_KNEE;
    return MOON_TONE_HIGHLIGHT_KNEE + excess / (1.0 + excess * MOON_TONE_HIGHLIGHT_COMPRESSION);
}

float moonApplyContrast(float value) {
    return clamp(MOON_TONE_PIVOT + (value - MOON_TONE_PIVOT) * MOON_TONE_CONTRAST, 0.0, 1.0);
}

float moonCompressLighting(float value) {
    if (value <= MOON_LIGHTING_HIGHLIGHT_KNEE) {
        return clamp(value, 0.0, 1.0);
    }
    float excess = value - MOON_LIGHTING_HIGHLIGHT_KNEE;
    return clamp(MOON_LIGHTING_HIGHLIGHT_KNEE + excess / (1.0 + excess * MOON_LIGHTING_HIGHLIGHT_COMPRESSION), 0.0, 1.0);
}

float moonResolvedDirectLight(float n_dot_l) {
    float soft_light = smoothstep(MOON_TERMINATOR_SOFT_START, MOON_TERMINATOR_SOFT_END, n_dot_l);
    float hemispheric_light = pow(clamp(max(n_dot_l, 0.0), 0.0, 1.0), MOON_LIGHTING_HEMISPHERE_GAMMA);
    return clamp(mix(soft_light, hemispheric_light, MOON_LIGHTING_HEMISPHERE_BLEND), 0.0, 1.0);
}

float moonAbsoluteLatitudeDegrees(vec2 uv) {
    return abs((0.5 - uv.y) * 180.0);
}

float moonPolarNormalFadeFactor(vec2 uv) {
    return smoothstep(MOON_POLAR_NORMAL_FADE_START_LAT_DEG, MOON_POLAR_NORMAL_FADE_END_LAT_DEG, moonAbsoluteLatitudeDegrees(uv));
}

float moonPolarAlbedoFadeFactor(vec2 uv) {
    return smoothstep(MOON_POLAR_ALBEDO_FADE_START_LAT_DEG, MOON_POLAR_ALBEDO_FADE_END_LAT_DEG, moonAbsoluteLatitudeDegrees(uv));
}

float moonSeamProximityFactor(vec2 uv) {
    float seam_distance = min(uv.x, 1.0 - uv.x);
    return 1.0 - smoothstep(0.0, MOON_SEAM_DIAGNOSTIC_WIDTH_UV, seam_distance);
}

vec3 sampleMoonPolarSafeGlobalAlbedo(vec2 uv, float polar_albedo_fade_factor) {
    vec3 base_sample = texture2D(u_albedo_texture, uv).rgb;
    if (polar_albedo_fade_factor <= 0.0) {
        return base_sample;
    }
    vec3 longitude_average = (
        texture2D(u_albedo_texture, vec2(fract(uv.x + 0.125), uv.y)).rgb
        + texture2D(u_albedo_texture, vec2(fract(uv.x + 0.375), uv.y)).rgb
        + texture2D(u_albedo_texture, vec2(fract(uv.x + 0.625), uv.y)).rgb
        + texture2D(u_albedo_texture, vec2(fract(uv.x + 0.875), uv.y)).rgb
    ) * 0.25;
    return mix(base_sample, longitude_average, polar_albedo_fade_factor);
}

float moonSmootherstep(float edge0, float edge1, float value) {
    float t = clamp((value - edge0) / max(edge1 - edge0, 1.0e-6), 0.0, 1.0);
    return t * t * t * (t * (t * 6.0 - 15.0) + 10.0);
}

float moonPolarCapBlendFactor(vec3 normal_moon) {
    float latitude_degrees = degrees(asin(clamp(abs(normal_moon.y), 0.0, 1.0)));
    float blend = moonSmootherstep(u_polarCapBlendStartLatDeg, u_polarCapBlendEndLatDeg, latitude_degrees);
    return pow(blend, max(u_polarCapBlendCurvePower, 0.1));
}

float moonPolarCapNormalBlendFactor(vec3 normal_moon) {
    float latitude_degrees = degrees(asin(clamp(abs(normal_moon.y), 0.0, 1.0)));
    return moonSmootherstep(u_polarCapNormalBlendStartLatDeg, u_polarCapNormalBlendEndLatDeg, latitude_degrees);
}

float moonPolarCapCoreFactor(vec3 normal_moon) {
    float latitude_degrees = degrees(asin(clamp(abs(normal_moon.y), 0.0, 1.0)));
    return moonSmootherstep(u_polarCapBlendEndLatDeg, 90.0, latitude_degrees);
}

vec2 moonPolarCapUv(vec3 normal_moon) {
    float angular_distance_degrees = 90.0 - degrees(asin(clamp(abs(normal_moon.y), 0.0, 1.0)));
    float radius = angular_distance_degrees / max(90.0 - u_polarCapLatitudeCoverageDeg, 1.0e-5);
    float horizontal_length = length(normal_moon.xz);
    vec2 direction = horizontal_length > 1.0e-6 ? vec2(normal_moon.x, -normal_moon.z) / horizontal_length : vec2(0.0, -1.0);
    return clamp(vec2(0.5) + direction * radius * 0.5, vec2(0.0), vec2(1.0));
}

bool moonPolarCapUvValid(vec2 cap_uv) {
    vec2 centered = (cap_uv - vec2(0.5)) * 2.0;
    return cap_uv.x >= 0.0 && cap_uv.x <= 1.0 && cap_uv.y >= 0.0 && cap_uv.y <= 1.0 && dot(centered, centered) <= 1.0001;
}

vec3 resolveMoonPolarCapAppearanceSample(
    vec3 base_sample,
    vec3 sample_x_neg,
    vec3 sample_x_pos,
    vec3 sample_y_neg,
    vec3 sample_y_pos,
    vec3 latlong_albedo,
    float boundary_factor,
    float core_factor
) {
    vec3 neighborhood_mean = (base_sample + sample_x_neg + sample_x_pos + sample_y_neg + sample_y_pos) * 0.2;
    vec3 sharpened = clamp(
        base_sample + (base_sample - neighborhood_mean) * max(u_polarCapAlbedoSharpness, 0.0),
        0.0,
        1.0
    );
    vec3 contrast_adjusted = clamp(
        neighborhood_mean + (sharpened - neighborhood_mean) * max(0.0, u_polarCapLocalContrast),
        0.0,
        1.0
    );
    const vec3 luma_weights = vec3(0.2126, 0.7152, 0.0722);
    float adjusted_luma = max(dot(contrast_adjusted, luma_weights), 1.0e-4);
    float latlong_luma = dot(latlong_albedo, luma_weights);
    float core_match_factor = clamp(u_polarCapCoreMatchStrength * core_factor, 0.0, 1.0);
    float luma_scale = clamp(latlong_luma / adjusted_luma, 0.88, 1.12);
    vec3 core_matched = clamp(contrast_adjusted * mix(1.0, luma_scale, core_match_factor), 0.0, 1.0);
    float boundary_match_factor = clamp(u_polarCapBoundaryMatchStrength * boundary_factor, 0.0, 1.0);
    return mix(core_matched, latlong_albedo, boundary_match_factor);
}

vec3 sampleMoonPolarCapAlbedo(vec3 normal_moon, vec3 latlong_albedo, out float cap_factor) {
    cap_factor = moonPolarCapBlendFactor(normal_moon);
    if (cap_factor <= 0.0) {
        return latlong_albedo;
    }
    vec2 cap_uv = moonPolarCapUv(normal_moon);
    if (!moonPolarCapUvValid(cap_uv)) {
        cap_factor = 0.0;
        return latlong_albedo;
    }
    float core_factor = moonPolarCapCoreFactor(normal_moon);
    float boundary_factor = cap_factor * (1.0 - core_factor);
    vec2 texel = vec2(1.0 / max(u_polarCapTextureSizePx, 1.0));
    if (normal_moon.y >= 0.0 && u_moonPolarCapNorthAlbedoEnabled != 0) {
        vec3 base_sample = texture2D(u_moonPolarCapNorthAlbedo, cap_uv).rgb;
        vec3 resolved_cap = resolveMoonPolarCapAppearanceSample(
            base_sample,
            texture2D(u_moonPolarCapNorthAlbedo, cap_uv - vec2(texel.x, 0.0)).rgb,
            texture2D(u_moonPolarCapNorthAlbedo, cap_uv + vec2(texel.x, 0.0)).rgb,
            texture2D(u_moonPolarCapNorthAlbedo, cap_uv - vec2(0.0, texel.y)).rgb,
            texture2D(u_moonPolarCapNorthAlbedo, cap_uv + vec2(0.0, texel.y)).rgb,
            latlong_albedo,
            boundary_factor,
            core_factor
        );
        return mix(latlong_albedo, resolved_cap, cap_factor);
    }
    if (normal_moon.y < 0.0 && u_moonPolarCapSouthAlbedoEnabled != 0) {
        vec3 base_sample = texture2D(u_moonPolarCapSouthAlbedo, cap_uv).rgb;
        vec3 resolved_cap = resolveMoonPolarCapAppearanceSample(
            base_sample,
            texture2D(u_moonPolarCapSouthAlbedo, cap_uv - vec2(texel.x, 0.0)).rgb,
            texture2D(u_moonPolarCapSouthAlbedo, cap_uv + vec2(texel.x, 0.0)).rgb,
            texture2D(u_moonPolarCapSouthAlbedo, cap_uv - vec2(0.0, texel.y)).rgb,
            texture2D(u_moonPolarCapSouthAlbedo, cap_uv + vec2(0.0, texel.y)).rgb,
            latlong_albedo,
            boundary_factor,
            core_factor
        );
        return mix(latlong_albedo, resolved_cap, cap_factor);
    }
    cap_factor = 0.0;
    return latlong_albedo;
}

vec3 sampleMoonPolarCapNormal(vec3 normal_moon, out float normal_available) {
    normal_available = 0.0;
    vec2 cap_uv = moonPolarCapUv(normal_moon);
    if (!moonPolarCapUvValid(cap_uv)) {
        return vec3(0.0, 0.0, 1.0);
    }
    if (normal_moon.y >= 0.0 && u_moonPolarCapNorthNormalEnabled != 0) {
        normal_available = 1.0;
        return texture2D(u_moonPolarCapNorthNormal, cap_uv).rgb * 2.0 - 1.0;
    }
    if (normal_moon.y < 0.0 && u_moonPolarCapSouthNormalEnabled != 0) {
        normal_available = 1.0;
        return texture2D(u_moonPolarCapSouthNormal, cap_uv).rgb * 2.0 - 1.0;
    }
    return vec3(0.0, 0.0, 1.0);
}

vec3 moonRemapAlbedo(vec3 albedo) {
    vec3 brightened = clamp(albedo * MOON_ALBEDO_BRIGHTNESS, 0.0, 1.5);
    vec3 contrasted = clamp(vec3(0.5) + (brightened - vec3(0.5)) * MOON_ALBEDO_CONTRAST, 0.0, 1.25);
    float luminance = dot(contrasted, vec3(0.2126, 0.7152, 0.0722));
    float separation_weight = smoothstep(0.12, 0.88, luminance);
    float remapped_luminance = clamp(
        luminance + (luminance - 0.46) * MOON_ALBEDO_MARE_HIGHLAND_CONTRAST_BOOST * separation_weight,
        0.0,
        1.0
    );
    return clamp(vec3(remapped_luminance) + (contrasted - vec3(luminance)) * MOON_ALBEDO_SATURATION_RETENTION, 0.0, 1.0);
}

vec3 moonToneMap(vec3 color) {
    vec3 exposed = max(color, vec3(0.0)) * MOON_TONE_EXPOSURE;
    vec3 compressed = vec3(
        moonCompressHighlight(exposed.r),
        moonCompressHighlight(exposed.g),
        moonCompressHighlight(exposed.b)
    );
    vec3 contrasted = vec3(
        moonApplyContrast(compressed.r),
        moonApplyContrast(compressed.g),
        moonApplyContrast(compressed.b)
    );
    float luminance = dot(contrasted, vec3(0.2126, 0.7152, 0.0722));
    float midtone_weight = smoothstep(0.08, 0.3, luminance) * (1.0 - smoothstep(0.72, 0.95, luminance));
    float chroma_gain = MOON_TONE_SATURATION + MOON_TONE_COLOR_PRESERVATION * midtone_weight;
    vec3 saturated = clamp(vec3(luminance) + (contrasted - vec3(luminance)) * chroma_gain, 0.0, 1.0);
    return pow(saturated, vec3(MOON_TONE_GAMMA));
}

vec3 sampleMoonAlbedo(vec2 uv, vec3 global_albedo, float polar_albedo_fade_factor, out int tile_index, out vec2 tile_local_uv, out float tile_weight) {
    tile_index = -1;
    tile_local_uv = vec2(0.0, 0.0);
    tile_weight = 0.0;
    if (u_moonTileAlbedoCount > 0 && moonTileContainsUv(uv, u_moonTileBounds0)) {
        tile_index = 0;
        tile_local_uv = moonTileLocalUv(uv, u_moonTileBounds0);
        tile_weight = moonTileEdgeWeight(tile_local_uv);
        vec3 tile_albedo = texture2D(u_moonTileAlbedo0, tile_local_uv).rgb;
        return mix(global_albedo, tile_albedo, (u_force_tiled_texture_only != 0 ? 1.0 : tile_weight) * (1.0 - polar_albedo_fade_factor));
    }
    if (u_moonTileAlbedoCount > 1 && moonTileContainsUv(uv, u_moonTileBounds1)) {
        tile_index = 1;
        tile_local_uv = moonTileLocalUv(uv, u_moonTileBounds1);
        tile_weight = moonTileEdgeWeight(tile_local_uv);
        vec3 tile_albedo = texture2D(u_moonTileAlbedo1, tile_local_uv).rgb;
        return mix(global_albedo, tile_albedo, (u_force_tiled_texture_only != 0 ? 1.0 : tile_weight) * (1.0 - polar_albedo_fade_factor));
    }
    if (u_moonTileAlbedoCount > 2 && moonTileContainsUv(uv, u_moonTileBounds2)) {
        tile_index = 2;
        tile_local_uv = moonTileLocalUv(uv, u_moonTileBounds2);
        tile_weight = moonTileEdgeWeight(tile_local_uv);
        vec3 tile_albedo = texture2D(u_moonTileAlbedo2, tile_local_uv).rgb;
        return mix(global_albedo, tile_albedo, (u_force_tiled_texture_only != 0 ? 1.0 : tile_weight) * (1.0 - polar_albedo_fade_factor));
    }
    if (u_moonTileAlbedoCount > 3 && moonTileContainsUv(uv, u_moonTileBounds3)) {
        tile_index = 3;
        tile_local_uv = moonTileLocalUv(uv, u_moonTileBounds3);
        tile_weight = moonTileEdgeWeight(tile_local_uv);
        vec3 tile_albedo = texture2D(u_moonTileAlbedo3, tile_local_uv).rgb;
        return mix(global_albedo, tile_albedo, (u_force_tiled_texture_only != 0 ? 1.0 : tile_weight) * (1.0 - polar_albedo_fade_factor));
    }
    if (u_moonTileAlbedoCount > 4 && moonTileContainsUv(uv, u_moonTileBounds4)) {
        tile_index = 4;
        tile_local_uv = moonTileLocalUv(uv, u_moonTileBounds4);
        tile_weight = moonTileEdgeWeight(tile_local_uv);
        vec3 tile_albedo = texture2D(u_moonTileAlbedo4, tile_local_uv).rgb;
        return mix(global_albedo, tile_albedo, (u_force_tiled_texture_only != 0 ? 1.0 : tile_weight) * (1.0 - polar_albedo_fade_factor));
    }
    if (u_moonTileAlbedoCount > 5 && moonTileContainsUv(uv, u_moonTileBounds5)) {
        tile_index = 5;
        tile_local_uv = moonTileLocalUv(uv, u_moonTileBounds5);
        tile_weight = moonTileEdgeWeight(tile_local_uv);
        vec3 tile_albedo = texture2D(u_moonTileAlbedo5, tile_local_uv).rgb;
        return mix(global_albedo, tile_albedo, (u_force_tiled_texture_only != 0 ? 1.0 : tile_weight) * (1.0 - polar_albedo_fade_factor));
    }
    return global_albedo;
}

vec3 clampMoonTiledNormalSlope(vec3 tile_sample) {
    vec3 resolved_sample = normalize(tile_sample);
    float tangent_length = length(resolved_sample.xy);
    float allowed_tangent_length = max(0.0, u_tiled_normal_max_slope) * max(resolved_sample.z, 1.0e-4);
    if (tangent_length > allowed_tangent_length && tangent_length > 1.0e-6) {
        resolved_sample.xy *= allowed_tangent_length / tangent_length;
    }
    return normalize(resolved_sample);
}

vec3 blendMoonTiledNormal(vec3 global_sample, vec3 tile_sample, float tile_weight, float polar_normal_fade_factor, out float tiled_normal_factor) {
    tiled_normal_factor = clamp(tile_weight * u_tiled_normal_fade_factor * (1.0 - polar_normal_fade_factor), 0.0, 1.0);
    return normalize(mix(global_sample, clampMoonTiledNormalSlope(tile_sample), tiled_normal_factor));
}

vec3 sampleMoonTerrainNormal(vec2 uv, int tile_index, vec2 tile_local_uv, float tile_weight, float polar_normal_fade_factor, out float tiled_normal_factor) {
    tiled_normal_factor = 0.0;
    vec3 global_sample = vec3(0.0, 0.0, 1.0);
    if (u_has_global_moon_normal_map != 0) {
        global_sample = texture2D(u_moon_normal_texture, uv).rgb * 2.0 - 1.0;
    }
    if (tile_index == 0 && u_moonTileNormalEnabled0 != 0) {
        vec3 tile_sample = texture2D(u_moonTileNormal0, tile_local_uv).rgb * 2.0 - 1.0;
        return blendMoonTiledNormal(global_sample, tile_sample, tile_weight, polar_normal_fade_factor, tiled_normal_factor);
    }
    if (tile_index == 1 && u_moonTileNormalEnabled1 != 0) {
        vec3 tile_sample = texture2D(u_moonTileNormal1, tile_local_uv).rgb * 2.0 - 1.0;
        return blendMoonTiledNormal(global_sample, tile_sample, tile_weight, polar_normal_fade_factor, tiled_normal_factor);
    }
    if (tile_index == 2 && u_moonTileNormalEnabled2 != 0) {
        vec3 tile_sample = texture2D(u_moonTileNormal2, tile_local_uv).rgb * 2.0 - 1.0;
        return blendMoonTiledNormal(global_sample, tile_sample, tile_weight, polar_normal_fade_factor, tiled_normal_factor);
    }
    if (tile_index == 3 && u_moonTileNormalEnabled3 != 0) {
        vec3 tile_sample = texture2D(u_moonTileNormal3, tile_local_uv).rgb * 2.0 - 1.0;
        return blendMoonTiledNormal(global_sample, tile_sample, tile_weight, polar_normal_fade_factor, tiled_normal_factor);
    }
    if (tile_index == 4 && u_moonTileNormalEnabled4 != 0) {
        vec3 tile_sample = texture2D(u_moonTileNormal4, tile_local_uv).rgb * 2.0 - 1.0;
        return blendMoonTiledNormal(global_sample, tile_sample, tile_weight, polar_normal_fade_factor, tiled_normal_factor);
    }
    if (tile_index == 5 && u_moonTileNormalEnabled5 != 0) {
        vec3 tile_sample = texture2D(u_moonTileNormal5, tile_local_uv).rgb * 2.0 - 1.0;
        return blendMoonTiledNormal(global_sample, tile_sample, tile_weight, polar_normal_fade_factor, tiled_normal_factor);
    }
    return global_sample;
}

void main() {
    vec2 disc = (gl_FragCoord.xy - u_center_px) / max(u_radius_px, 1.0e-4);
    float r2 = dot(disc, disc);
    if (r2 > 1.0) {
        discard;
    }

    float z = sqrt(max(0.0, 1.0 - r2));
    vec3 normal_view = normalize(vec3(disc.x, disc.y, z));
    vec3 normal_moon = normalize(vec3(
        dot(u_view_to_moon_row0, normal_view),
        dot(u_view_to_moon_row1, normal_view),
        dot(u_view_to_moon_row2, normal_view)
    ));

    float longitude_rad = atan(normal_moon.x, normal_moon.z);
    float latitude_rad = asin(clamp(normal_moon.y, -1.0, 1.0));
    vec2 uv = vec2(fract(0.5 + longitude_rad / TAU), clamp(0.5 - latitude_rad / PI, 0.0, 1.0));
    float polar_normal_fade_factor = moonPolarNormalFadeFactor(uv);
    float polar_albedo_fade_factor = moonPolarAlbedoFadeFactor(uv);
    float seam_proximity_factor = moonSeamProximityFactor(uv);
    vec3 global_albedo = sampleMoonPolarSafeGlobalAlbedo(uv, polar_albedo_fade_factor);
    int tile_index;
    vec2 tile_local_uv;
    float tile_weight;
    vec3 resolved_albedo_sample = sampleMoonAlbedo(uv, global_albedo, polar_albedo_fade_factor, tile_index, tile_local_uv, tile_weight);
    float polar_cap_blend_factor;
    resolved_albedo_sample = sampleMoonPolarCapAlbedo(normal_moon, resolved_albedo_sample, polar_cap_blend_factor);
    vec3 albedo = moonRemapAlbedo(resolved_albedo_sample);

    vec3 sun_dir = normalize(u_sun_dir_moon);
    float n_dot_l = dot(normal_moon, sun_dir);
    float direct_light = moonResolvedDirectLight(n_dot_l);
    vec3 terrain_normal = normal_moon;
    if (u_has_moon_normal_map != 0 && u_terrain_normal_strength > 0.001) {
        float tiled_normal_factor;
        vec3 terrain_sample = sampleMoonTerrainNormal(uv, tile_index, tile_local_uv, tile_weight, polar_normal_fade_factor, tiled_normal_factor);
        float analytic_normal_factor = MOON_POLAR_USE_ANALYTIC_NORMAL > 0.5 ? (1.0 - polar_normal_fade_factor) : 1.0;
        float resolved_normal_strength = mix(u_terrain_normal_strength_global, u_tiled_normal_strength, tiled_normal_factor);
        float resolved_terminator_strength = mix(u_terrain_normal_strength_terminator, u_tiled_terminator_normal_strength, tiled_normal_factor);
        vec3 tangent_lon = normalize(vec3(normal_moon.z, 0.0, -normal_moon.x));
        if (dot(tangent_lon, tangent_lon) < 0.01) {
            tangent_lon = vec3(1.0, 0.0, 0.0);
        }
        vec3 tangent_lat = normalize(cross(tangent_lon, normal_moon));
        terrain_normal = normalize(
            normal_moon * max(MOON_TERRAIN_NORMAL_Z_FLOOR, terrain_sample.z)
            + tangent_lon * terrain_sample.x * resolved_normal_strength * analytic_normal_factor
            + tangent_lat * terrain_sample.y * resolved_normal_strength * analytic_normal_factor
        );
        float polar_cap_normal_available;
        vec3 polar_cap_sample = sampleMoonPolarCapNormal(normal_moon, polar_cap_normal_available);
        float polar_cap_normal_blend_factor = moonPolarCapNormalBlendFactor(normal_moon);
        float polar_cap_normal_factor = polar_cap_normal_blend_factor * polar_cap_normal_available;
        vec3 polar_cap_tangent_x = vec3(1.0, 0.0, 0.0) - normal_moon * normal_moon.x;
        if (dot(polar_cap_tangent_x, polar_cap_tangent_x) < 1.0e-6) {
            polar_cap_tangent_x = vec3(0.0, 0.0, 1.0);
        }
        polar_cap_tangent_x = normalize(polar_cap_tangent_x);
        vec3 polar_cap_tangent_y = normalize(cross(normal_moon, polar_cap_tangent_x) * (normal_moon.y >= 0.0 ? 1.0 : -1.0));
        vec3 polar_cap_terrain_normal = normalize(
            normal_moon * max(MOON_TERRAIN_NORMAL_Z_FLOOR, polar_cap_sample.z)
            + polar_cap_tangent_x * polar_cap_sample.x * u_polarCapNormalStrength
            + polar_cap_tangent_y * polar_cap_sample.y * u_polarCapNormalStrength
        );
        terrain_normal = normalize(mix(terrain_normal, polar_cap_terrain_normal, polar_cap_normal_factor));
        float terrain_direct = moonResolvedDirectLight(dot(terrain_normal, sun_dir));
        float terminator_weight = 1.0 - smoothstep(MOON_TERMINATOR_BAND_WIDTH, MOON_TERMINATOR_BAND_WIDTH + MOON_TERMINATOR_SOFTNESS, abs(n_dot_l));
        float terminator_normal_factor = mix(
            MOON_TERMINATOR_NORMAL_MIN_FACTOR,
            1.0,
            smoothstep(MOON_TERMINATOR_NORMAL_LOW_LIGHT_START, MOON_TERMINATOR_NORMAL_LOW_LIGHT_END, n_dot_l)
        );
        float polar_relief_factor = mix(analytic_normal_factor, polar_cap_normal_available, polar_cap_normal_blend_factor);
        float terrain_blend = clamp(
            (resolved_normal_strength * 0.42 + terminator_weight * resolved_terminator_strength) * terminator_normal_factor * polar_relief_factor,
            0.0,
            MOON_TERRAIN_BLEND_CAP
        );
        direct_light = mix(direct_light, terrain_direct, terrain_blend);
    }
    vec3 earth_dir = normalize(u_earth_dir_moon);
    float earth_facing = max(0.0, dot(normal_moon, earth_dir));
    float earth_facing_weight = pow(earth_facing, MOON_EARTHSHINE_DIRECTIONAL_GAMMA);
    float earth_shadow_weight = 1.0 - smoothstep(MOON_EARTHSHINE_SHADOW_START, MOON_EARTHSHINE_SHADOW_END, n_dot_l);
    float earthshine = clamp((u_earthshine_floor * mix(0.18, 1.0, earth_facing_weight) + u_earthshine_strength * earth_facing_weight) * earth_shadow_weight, 0.0, MOON_EARTHSHINE_MAX);
    float lighting_scalar = moonCompressLighting(max(0.0, earthshine + direct_light * MOON_DIRECT_LIGHT_GAIN));
    float limb = smoothstep(0.0, MOON_LIMB_SOFTNESS, 1.0 - r2);
    if (u_debug_render_mode == 1) {
        gl_FragColor = vec4(clamp(albedo, 0.0, 1.0), clamp(u_opacity, 0.0, 1.0) * limb);
        return;
    }
    if (u_debug_render_mode == 2) {
        gl_FragColor = vec4(vec3(lighting_scalar), clamp(u_opacity, 0.0, 1.0) * limb);
        return;
    }
    if (u_debug_render_mode == 3) {
        gl_FragColor = vec4(clamp(terrain_normal * 0.5 + 0.5, 0.0, 1.0), clamp(u_opacity, 0.0, 1.0) * limb);
        return;
    }
    if (u_debug_render_mode == 4) {
        vec3 uv_debug_color = vec3(uv.x, uv.y, 1.0 - abs(uv.y - 0.5) * 2.0);
        float pole_marker = smoothstep(0.94, 1.0, polar_normal_fade_factor);
        float seam_marker = smoothstep(0.72, 1.0, seam_proximity_factor) * (1.0 - pole_marker);
        uv_debug_color = mix(uv_debug_color, vec3(1.0, 0.1, 0.85), pole_marker);
        uv_debug_color = mix(uv_debug_color, vec3(0.05, 1.0, 1.0), seam_marker);
        gl_FragColor = vec4(uv_debug_color, clamp(u_opacity, 0.0, 1.0) * limb);
        return;
    }
    if (u_debug_render_mode == 5) {
        bool tile_route_error = tile_index >= 0 && (
            tile_local_uv.x < -1.0e-5 || tile_local_uv.x > 1.00001 ||
            tile_local_uv.y < -1.0e-5 || tile_local_uv.y > 1.00001
        );
        vec3 polar_route_color = mix(vec3(0.0, 1.0, 0.0), vec3(0.0, 0.3, 1.0), polar_albedo_fade_factor);
        vec3 route_color = tile_route_error ? vec3(1.0, 0.0, 0.0) : (tile_index >= 0 ? polar_route_color : vec3(0.0, 0.3, 1.0));
        gl_FragColor = vec4(route_color, clamp(u_opacity, 0.0, 1.0) * limb);
        return;
    }
    if (u_debug_render_mode == 6) {
        gl_FragColor = vec4(clamp(abs(resolved_albedo_sample - global_albedo) * 8.0, 0.0, 1.0), clamp(u_opacity, 0.0, 1.0) * limb);
        return;
    }
    if (u_debug_render_mode == 7) {
        vec3 polar_cap_route = polar_cap_blend_factor <= 0.0
            ? vec3(0.0, 0.3, 1.0)
            : (polar_cap_blend_factor >= 0.999 ? vec3(0.0, 1.0, 0.0) : vec3(1.0, 0.82, 0.0));
        gl_FragColor = vec4(polar_cap_route, clamp(u_opacity, 0.0, 1.0) * limb);
        return;
    }
    if (u_debug_render_mode == 8) {
        float polar_cap_core_factor = moonPolarCapCoreFactor(normal_moon);
        float polar_cap_blend_only = clamp(polar_cap_blend_factor - polar_cap_core_factor, 0.0, 1.0);
        vec3 footprint_route = vec3(0.0, 0.3, 1.0);
        footprint_route = mix(footprint_route, vec3(1.0, 0.82, 0.0), polar_cap_blend_only);
        footprint_route = mix(footprint_route, vec3(1.0, 0.14, 0.08), polar_cap_core_factor);
        gl_FragColor = vec4(footprint_route, clamp(u_opacity, 0.0, 1.0) * limb);
        return;
    }
    vec3 lit_color = albedo * lighting_scalar;
    lit_color *= clamp(u_atmosphere_extinction, 0.0, 1.2) * clamp(u_atmosphere_tint_rgb, 0.0, 1.2);
    lit_color = u_disable_tone_mapping != 0 ? clamp(lit_color, 0.0, 1.0) : moonToneMap(lit_color);
    gl_FragColor = vec4(lit_color, clamp(u_opacity, 0.0, 1.0) * limb);
}
"""
    )

    def __init__(self) -> None:

        self._program: QOpenGLShaderProgram | None = None
        self._uniform_locations: dict[str, int] = {}
        self._textures: OrderedDict[tuple[object, ...], _MoonTextureCacheEntry] = OrderedDict()
        self._texture_cache_total_bytes = 0
        self.last_program_init_seconds = 0.0
        self.last_texture_upload_seconds = 0.0
        self.last_draw_seconds = 0.0
        self.last_lod_label = "none"
        self.last_texture_uploaded = False
        self.last_texture_cache_entries = 0
        self.last_texture_cache_bytes = 0
        self.last_tiled_shader_active = False
        self.last_bound_tile_count = 0
        self.last_bound_albedo_tile_count = 0
        self.last_bound_normal_tile_count = 0
        self.last_bound_polar_cap_texture_count = 0
        self.last_texture_bind_count = 0
        self.last_draw_call_count = 0
        self.last_selected_bound_tile_keys: tuple[str, ...] = ()
        self.last_debug_render_mode = "normal"
        self._last_tile_cache_generation_id = ""

    def initialize(self, functions: QOpenGLFunctions_2_0 | None) -> bool:

        self.last_program_init_seconds = 0.0
        if functions is None:
            return False
        if self._program is not None:
            return True

        benchmark_recorder = get_benchmark_recorder() if BENCHMARK_ENABLED else None

        benchmark_token = benchmark_recorder.start_section("moon.gl_program_init") if benchmark_recorder is not None else None

        init_start = perf_counter()
        program = QOpenGLShaderProgram()
        if not program.addShaderFromSourceCode(QOpenGLShader.ShaderTypeBit.Vertex, self._VERTEX_SHADER_SOURCE):
            raise RuntimeError(f"Failed to compile Moon vertex shader: {program.log().strip() or 'unknown shader compile error'}")
        if not program.addShaderFromSourceCode(QOpenGLShader.ShaderTypeBit.Fragment, self._FRAGMENT_SHADER_SOURCE):
            raise RuntimeError(f"Failed to compile Moon fragment shader: {program.log().strip() or 'unknown shader compile error'}")
        if not program.link():
            raise RuntimeError(f"Failed to link Moon shader program: {program.log().strip() or 'unknown shader link error'}")
        uniform_locations = {
            name: int(program.uniformLocation(name))
            for name in (
                "u_center_px",
                "u_radius_px",
                "u_opacity",
                "u_sun_dir_moon",
                "u_earth_dir_moon",
                "u_view_to_moon_row0",
                "u_view_to_moon_row1",
                "u_view_to_moon_row2",
                "u_albedo_texture",
                "u_moon_normal_texture",
                "u_has_moon_normal_map",
                "u_has_global_moon_normal_map",
                "u_terrain_normal_strength",
                "u_terrain_normal_strength_global",
                "u_terrain_normal_strength_terminator",
                "u_tiled_normal_strength",
                "u_tiled_terminator_normal_strength",
                "u_tiled_normal_fade_factor",
                "u_tiled_normal_max_slope",
                "u_earthshine_strength",
                "u_earthshine_floor",
                "u_atmosphere_extinction",
                "u_atmosphere_tint_rgb",
                "u_debug_render_mode",
                "u_disable_tone_mapping",
                "u_force_tiled_texture_only",
                "u_moonTileAlbedoCount",
                "u_moonTileEdgeFade",
                "u_moonTileAlbedo0",
                "u_moonTileAlbedo1",
                "u_moonTileAlbedo2",
                "u_moonTileAlbedo3",
                "u_moonTileAlbedo4",
                "u_moonTileAlbedo5",
                "u_moonTileNormal0",
                "u_moonTileNormal1",
                "u_moonTileNormal2",
                "u_moonTileNormal3",
                "u_moonTileNormal4",
                "u_moonTileNormal5",
                "u_moonTileBounds0",
                "u_moonTileBounds1",
                "u_moonTileBounds2",
                "u_moonTileBounds3",
                "u_moonTileBounds4",
                "u_moonTileBounds5",
                "u_moonTileNormalEnabled0",
                "u_moonTileNormalEnabled1",
                "u_moonTileNormalEnabled2",
                "u_moonTileNormalEnabled3",
                "u_moonTileNormalEnabled4",
                "u_moonTileNormalEnabled5",
                "u_moonPolarCapNorthAlbedo",
                "u_moonPolarCapSouthAlbedo",
                "u_moonPolarCapNorthNormal",
                "u_moonPolarCapSouthNormal",
                "u_moonPolarCapNorthAlbedoEnabled",
                "u_moonPolarCapSouthAlbedoEnabled",
                "u_moonPolarCapNorthNormalEnabled",
                "u_moonPolarCapSouthNormalEnabled",
                "u_polarCapNormalStrength",
                "u_polarCapBlendStartLatDeg",
                "u_polarCapBlendEndLatDeg",
                "u_polarCapBlendCurvePower",
                "u_polarCapNormalBlendStartLatDeg",
                "u_polarCapNormalBlendEndLatDeg",
                "u_polarCapLatitudeCoverageDeg",
                "u_polarCapTextureSizePx",
                "u_polarCapAlbedoSharpness",
                "u_polarCapLocalContrast",
                "u_polarCapBoundaryMatchStrength",
                "u_polarCapCoreMatchStrength",
            )
        }
        missing_uniforms = sorted(name for name, location in uniform_locations.items() if location < 0)
        if missing_uniforms:
            raise RuntimeError("Moon shader is missing uniforms: " + ", ".join(missing_uniforms))
        self._program = program
        self._uniform_locations = uniform_locations
        self.last_program_init_seconds = perf_counter() - init_start
        if benchmark_recorder is not None:
            benchmark_recorder.stop_section(benchmark_token, metadata={"initialized": True})
        return True

    def release(self) -> None:

        self._destroy_textures()
        self._last_tile_cache_generation_id = ""
        program = self._program
        self._program = None
        self._uniform_locations = {}
        if program is None:
            return
        try:
            program.release()
        except Exception:
            pass
        try:
            program.removeAllShaders()
        except Exception:
            pass

    def synchronize_tile_cache_generation(self, generation_id: str) -> None:

        resolved_generation_id = str(generation_id or "")
        if resolved_generation_id == self._last_tile_cache_generation_id:
            return
        self._last_tile_cache_generation_id = resolved_generation_id
        tile_keys = [
            cache_key
            for cache_key in self._textures
            if cache_key and cache_key[0] in {"moon-tile", "moon-polar-cap"}
        ]
        for cache_key in tile_keys:
            entry = self._textures.pop(cache_key)
            self._texture_cache_total_bytes = max(0, self._texture_cache_total_bytes - entry.approx_bytes)
            self._release_texture(entry.texture)

    def draw(
        self,
        functions: QOpenGLFunctions_2_0 | None,
        draw_state: MoonDrawState,
        texture_lod: MoonTextureLod,
        *,
        normal_texture_lod: MoonTextureLod | None = None,
        tile_bindings: Sequence[MoonTileShaderBinding] = (),
        polar_cap_binding: MoonPolarCapBinding | None = None,
        tile_edge_fade_fraction: float = MoonTileSettings().tile_edge_fade_fraction,
        force_tiled_texture_only: bool = False,
        cache_budget_bytes: int = 160 * 1024 * 1024,
    ) -> bool:

        benchmark_recorder = get_benchmark_recorder() if BENCHMARK_ENABLED else None

        benchmark_draw_token = benchmark_recorder.start_section("moon.renderer_gl.draw") if benchmark_recorder is not None else None

        self.last_texture_upload_seconds = 0.0
        self.last_draw_seconds = 0.0
        self.last_texture_uploaded = False
        self.last_lod_label = texture_lod.label
        self.last_tiled_shader_active = False
        self.last_bound_tile_count = 0
        self.last_bound_albedo_tile_count = 0
        self.last_bound_normal_tile_count = 0
        self.last_bound_polar_cap_texture_count = 0
        self.last_texture_bind_count = 0
        self.last_draw_call_count = 0
        self.last_selected_bound_tile_keys = ()
        self.last_debug_render_mode = draw_state.debug_render_mode
        if functions is None:
            if benchmark_recorder is not None:
                benchmark_recorder.stop_section(benchmark_draw_token, metadata={"error": "missing_gl_functions"})
            raise RuntimeError("OpenGL functions are unavailable")
        benchmark_visibility_token = benchmark_recorder.start_section("moon.visibility_culling", metadata={"radius_px": float(draw_state.radius_px)}) if benchmark_recorder is not None else None
        if draw_state.radius_px <= 0.5 or texture_lod.image.isNull():
            if benchmark_recorder is not None:
                benchmark_recorder.stop_section(benchmark_visibility_token, metadata={"visible": False})
                benchmark_recorder.stop_section(benchmark_draw_token, metadata={"drawn": False, "reason": "empty_disc_or_texture"})
            return False
        if benchmark_recorder is not None:
            benchmark_recorder.stop_section(benchmark_visibility_token, metadata={"visible": True})
        if not self.initialize(functions):
            if benchmark_recorder is not None:
                benchmark_recorder.stop_section(benchmark_draw_token, metadata={"drawn": False, "reason": "renderer_unavailable"})
            return False
        program = self._program
        if program is None:
            raise RuntimeError("Moon shader program is unavailable")

        benchmark_route_token = benchmark_recorder.start_section("moon.route_selection") if benchmark_recorder is not None else None
        active_tile_bindings = tuple(tile_bindings[: self.MOON_TILE_SAMPLER_LIMIT])
        active_polar_cap_binding = polar_cap_binding
        use_global_normal_map = normal_texture_lod is not None and not normal_texture_lod.image.isNull() and draw_state.terrain_normal_strength > 0.0
        tile_normal_count = sum(1 for binding in active_tile_bindings if binding.normal_tile is not None and not binding.normal_tile.image.isNull())
        polar_cap_normal_count = sum(
            1
            for lod in (
                None if active_polar_cap_binding is None else active_polar_cap_binding.north_normal,
                None if active_polar_cap_binding is None else active_polar_cap_binding.south_normal,
            )
            if lod is not None and not lod.image.isNull()
        )
        use_any_normal_map = (use_global_normal_map or tile_normal_count > 0 or polar_cap_normal_count > 0) and draw_state.terrain_normal_strength > 0.0
        if benchmark_recorder is not None:
            benchmark_recorder.stop_section(
                benchmark_route_token,
                metadata={
                    "tile_bindings": len(active_tile_bindings),
                    "tile_normal_count": tile_normal_count,
                    "polar_cap_normal_count": polar_cap_normal_count,
                    "use_global_normal_map": use_global_normal_map,
                    "use_any_normal_map": use_any_normal_map,
                    "force_tiled_texture_only": force_tiled_texture_only or draw_state.force_tiled_texture_only,
                },
            )
        benchmark_texture_prepare_token = benchmark_recorder.start_section("moon.texture_prepare", metadata={"tile_bindings": len(active_tile_bindings)}) if benchmark_recorder is not None else None
        protected_cache_keys = self.protected_cache_keys_for_draw(
            texture_lod,
            normal_texture_lod if use_global_normal_map else None,
            active_tile_bindings,
            active_polar_cap_binding,
        )
        texture_entry = self._ensure_texture(texture_lod, cache_budget_bytes=cache_budget_bytes, protected_cache_keys=protected_cache_keys)
        normal_texture_entry = (
            self._ensure_texture(normal_texture_lod, cache_budget_bytes=cache_budget_bytes, protected_cache_keys=protected_cache_keys)
            if use_global_normal_map and normal_texture_lod is not None
            else None
        )
        tile_albedo_entries: list[_MoonTextureCacheEntry] = []
        tile_normal_entries: list[_MoonTextureCacheEntry | None] = []
        for binding in active_tile_bindings:
            tile_albedo_entries.append(self._ensure_texture(binding.albedo_tile, cache_budget_bytes=cache_budget_bytes, protected_cache_keys=protected_cache_keys))
            tile_normal_entries.append(
                self._ensure_texture(binding.normal_tile, cache_budget_bytes=cache_budget_bytes, protected_cache_keys=protected_cache_keys)
                if binding.normal_tile is not None and not binding.normal_tile.image.isNull()
                else None
            )
        polar_cap_entries: dict[str, _MoonTextureCacheEntry | None] = {}
        for label, lod in (
            ("north_albedo", None if active_polar_cap_binding is None else active_polar_cap_binding.north_albedo),
            ("south_albedo", None if active_polar_cap_binding is None else active_polar_cap_binding.south_albedo),
            ("north_normal", None if active_polar_cap_binding is None else active_polar_cap_binding.north_normal),
            ("south_normal", None if active_polar_cap_binding is None else active_polar_cap_binding.south_normal),
        ):
            polar_cap_entries[label] = (
                self._ensure_texture(lod, cache_budget_bytes=cache_budget_bytes, protected_cache_keys=protected_cache_keys)
                if lod is not None and not lod.image.isNull()
                else None
            )
        if benchmark_recorder is not None:
            benchmark_recorder.stop_section(
                benchmark_texture_prepare_token,
                metadata={
                    "texture_uploaded": self.last_texture_uploaded,
                    "texture_upload_seconds": self.last_texture_upload_seconds,
                    "texture_cache_entries": len(self._textures),
                },
            )
        self.last_tiled_shader_active = bool(tile_albedo_entries)
        self.last_bound_tile_count = len(tile_albedo_entries)
        self.last_bound_albedo_tile_count = len(tile_albedo_entries)
        self.last_bound_normal_tile_count = sum(1 for entry in tile_normal_entries if entry is not None)
        self.last_bound_polar_cap_texture_count = sum(1 for entry in polar_cap_entries.values() if entry is not None)
        self.last_selected_bound_tile_keys = tuple(MoonTiledTextureManager._format_tile_key(binding.key) for binding in active_tile_bindings[: len(tile_albedo_entries)])
        benchmark_state_token = benchmark_recorder.start_section("moon.capture_previous_gl_state") if benchmark_recorder is not None else None
        previous_viewport = self._integer_values(functions, self._GL_VIEWPORT, 4)
        previous_program = self._integer_values(functions, self._GL_CURRENT_PROGRAM, 1)
        previous_blend_src = self._integer_values(functions, self._GL_BLEND_SRC, 1)
        previous_blend_dst = self._integer_values(functions, self._GL_BLEND_DST, 1)
        previous_active_texture = self._integer_values(functions, self._GL_ACTIVE_TEXTURE, 1)
        texture_units_to_restore = set(range(2 + self.MOON_TILE_SAMPLER_LIMIT * 2)) if tile_albedo_entries else {0, 1}
        if any(entry is not None for entry in polar_cap_entries.values()):
            texture_units_to_restore.update(range(2 + self.MOON_TILE_SAMPLER_LIMIT * 2, 2 + self.MOON_TILE_SAMPLER_LIMIT * 2 + 4))
        previous_texture_bindings: dict[int, tuple[int, ...] | None] = {}
        if previous_active_texture is not None:
            try:
                for unit_index in sorted(texture_units_to_restore):
                    getattr(functions, "glActiveTexture")(self._texture_unit(unit_index))
                    previous_texture_bindings[unit_index] = self._integer_values(functions, self._GL_TEXTURE_BINDING_2D, 1)
            finally:
                getattr(functions, "glActiveTexture")(int(previous_active_texture[0]))
        previous_enabled_states = {
            self._GL_BLEND: self._is_enabled(functions, self._GL_BLEND),
            self._GL_DEPTH_TEST: self._is_enabled(functions, self._GL_DEPTH_TEST),
            self._GL_SCISSOR_TEST: self._is_enabled(functions, self._GL_SCISSOR_TEST),
            self._GL_STENCIL_TEST: self._is_enabled(functions, self._GL_STENCIL_TEST),
            self._GL_TEXTURE_2D: self._is_enabled(functions, self._GL_TEXTURE_2D),
        }
        if benchmark_recorder is not None:
            benchmark_recorder.stop_section(benchmark_state_token, metadata={"texture_units": len(texture_units_to_restore)})
        program_bound = False
        texture_bound = False
        normal_texture_bound = False
        draw_start = perf_counter()
        try:
            self._check_gl_error(functions, "before Moon draw")
            functions.glViewport(0, 0, int(draw_state.viewport_width_px), int(draw_state.viewport_height_px))
            functions.glEnable(self._GL_BLEND)
            functions.glBlendFunc(self._GL_SRC_ALPHA, self._GL_ONE_MINUS_SRC_ALPHA)
            functions.glDisable(self._GL_DEPTH_TEST)
            functions.glDisable(self._GL_SCISSOR_TEST)
            functions.glDisable(self._GL_STENCIL_TEST)
            benchmark_bind_token = benchmark_recorder.start_section("moon.texture_bind", metadata={"tile_textures": len(tile_albedo_entries), "polar_cap_textures": self.last_bound_polar_cap_texture_count}) if benchmark_recorder is not None else None
            getattr(functions, "glActiveTexture")(self._GL_TEXTURE0)
            functions.glEnable(self._GL_TEXTURE_2D)
            texture_entry.texture.bind()
            self.last_texture_bind_count += 1
            texture_bound = True
            if normal_texture_entry is not None:
                getattr(functions, "glActiveTexture")(self._GL_TEXTURE1)
                functions.glEnable(self._GL_TEXTURE_2D)
                normal_texture_entry.texture.bind()
                self.last_texture_bind_count += 1
                normal_texture_bound = True
                getattr(functions, "glActiveTexture")(self._GL_TEXTURE0)
            tile_texture_bindings: list[tuple[_MoonTextureCacheEntry, int]] = []
            for tile_index, tile_entry in enumerate(tile_albedo_entries):
                albedo_unit = 2 + tile_index
                getattr(functions, "glActiveTexture")(self._texture_unit(albedo_unit))
                functions.glEnable(self._GL_TEXTURE_2D)
                tile_entry.texture.bind()
                self.last_texture_bind_count += 1
                tile_texture_bindings.append((tile_entry, albedo_unit))
            for tile_index, tile_entry in enumerate(tile_normal_entries):
                if tile_entry is None:
                    continue
                normal_unit = 2 + self.MOON_TILE_SAMPLER_LIMIT + tile_index
                getattr(functions, "glActiveTexture")(self._texture_unit(normal_unit))
                functions.glEnable(self._GL_TEXTURE_2D)
                tile_entry.texture.bind()
                self.last_texture_bind_count += 1
                tile_texture_bindings.append((tile_entry, normal_unit))
            polar_cap_texture_bindings: list[tuple[_MoonTextureCacheEntry, int]] = []
            polar_cap_unit_base = 2 + self.MOON_TILE_SAMPLER_LIMIT * 2
            for unit_offset, label in enumerate(("north_albedo", "south_albedo", "north_normal", "south_normal")):
                cap_entry = polar_cap_entries[label]
                if cap_entry is None:
                    continue
                unit_index = polar_cap_unit_base + unit_offset
                getattr(functions, "glActiveTexture")(self._texture_unit(unit_index))
                functions.glEnable(self._GL_TEXTURE_2D)
                cap_entry.texture.bind()
                self.last_texture_bind_count += 1
                polar_cap_texture_bindings.append((cap_entry, unit_index))
            getattr(functions, "glActiveTexture")(self._GL_TEXTURE0)
            if benchmark_recorder is not None:
                benchmark_recorder.stop_section(benchmark_bind_token, metadata={"texture_binds": self.last_texture_bind_count})
            benchmark_shader_token = benchmark_recorder.start_section("moon.shader_setup") if benchmark_recorder is not None else None
            if not program.bind():
                if benchmark_recorder is not None:
                    benchmark_recorder.stop_section(benchmark_shader_token, metadata={"error": "program_bind_failed"})
                raise RuntimeError(f"Failed to bind Moon shader program: {program.log().strip() or 'unknown shader bind error'}")
            program_bound = True
            if benchmark_recorder is not None:
                benchmark_recorder.stop_section(benchmark_shader_token)
            benchmark_uniform_token = benchmark_recorder.start_section("moon.phase_shading_uniforms", metadata={"has_normal_map": use_any_normal_map}) if benchmark_recorder is not None else None
            self._set_uniform_values(
                functions,
                draw_state,
                has_normal_map=use_any_normal_map,
                has_global_normal_map=normal_texture_entry is not None,
                tile_bindings=active_tile_bindings[: len(tile_albedo_entries)],
                polar_cap_binding=active_polar_cap_binding,
                tile_edge_fade_fraction=tile_edge_fade_fraction,
                force_tiled_texture_only=force_tiled_texture_only or draw_state.force_tiled_texture_only,
            )
            if benchmark_recorder is not None:
                benchmark_recorder.stop_section(
                    benchmark_uniform_token,
                    metadata={
                        "phase_angle_deg": float(getattr(draw_state, "phase_angle_deg", 0.0)),
                        "terrain_normal_strength": float(draw_state.terrain_normal_strength),
                        "bound_tiles": self.last_bound_tile_count,
                        "polar_cap_textures": self.last_bound_polar_cap_texture_count,
                    },
                )
            benchmark_disc_token = benchmark_recorder.start_section("moon.draw_call", metadata={"radius_px": float(draw_state.radius_px)}) if benchmark_recorder is not None else None
            self._draw_disc_quad(functions, draw_state)
            self.last_draw_call_count += 1
            if benchmark_recorder is not None:
                benchmark_recorder.stop_section(benchmark_disc_token, metadata={"draw_calls": self.last_draw_call_count})
            self._check_gl_error(functions, "after Moon draw")
            self.last_draw_seconds = perf_counter() - draw_start
            self.last_texture_cache_entries = len(self._textures)
            self.last_texture_cache_bytes = self._texture_cache_total_bytes
            return True
        finally:
            if program_bound:
                try:
                    program.release()
                except Exception:
                    pass
            if texture_bound:
                try:
                    texture_entry.texture.release()
                except Exception:
                    pass
            if normal_texture_bound and normal_texture_entry is not None:
                try:
                    normal_texture_entry.texture.release()
                except Exception:
                    pass
            for tile_entry, _unit_index in locals().get("tile_texture_bindings", []):
                try:
                    tile_entry.texture.release()
                except Exception:
                    pass
            for cap_entry, _unit_index in locals().get("polar_cap_texture_bindings", []):
                try:
                    cap_entry.texture.release()
                except Exception:
                    pass
            if previous_active_texture is not None:
                for unit_index, previous_binding in previous_texture_bindings.items():
                    getattr(functions, "glActiveTexture")(self._texture_unit(unit_index))
                    if previous_binding is not None:
                        functions.glBindTexture(self._GL_TEXTURE_2D, int(previous_binding[0]))
                getattr(functions, "glActiveTexture")(int(previous_active_texture[0]))
            if previous_program is not None:
                functions.glUseProgram(int(previous_program[0]))
            if previous_blend_src is not None and previous_blend_dst is not None:
                functions.glBlendFunc(int(previous_blend_src[0]), int(previous_blend_dst[0]))
            self._restore_enabled_states(functions, previous_enabled_states)
            if previous_viewport is not None:
                functions.glViewport(int(previous_viewport[0]), int(previous_viewport[1]), int(previous_viewport[2]), int(previous_viewport[3]))
            if benchmark_recorder is not None:
                benchmark_recorder.stop_section(
                    benchmark_draw_token,
                    metadata={
                        "drawn": self.last_draw_seconds > 0.0,
                        "lod_label": self.last_lod_label,
                        "texture_uploaded": self.last_texture_uploaded,
                        "texture_upload_seconds": self.last_texture_upload_seconds,
                        "bound_tiles": self.last_bound_tile_count,
                        "bound_albedo_tiles": self.last_bound_albedo_tile_count,
                        "bound_normal_tiles": self.last_bound_normal_tile_count,
                        "bound_polar_cap_textures": self.last_bound_polar_cap_texture_count,
                        "texture_binds": self.last_texture_bind_count,
                        "draw_calls": self.last_draw_call_count,
                        "tiled_shader_active": self.last_tiled_shader_active,
                    },
                )

    def _set_uniform_values(
        self,
        functions: QOpenGLFunctions_2_0,
        draw_state: MoonDrawState,
        *,
        has_normal_map: bool,
        has_global_normal_map: bool = False,
        tile_bindings: Sequence[MoonTileShaderBinding] = (),
        polar_cap_binding: MoonPolarCapBinding | None = None,
        tile_edge_fade_fraction: float = MoonTileSettings().tile_edge_fade_fraction,
        force_tiled_texture_only: bool = False,
    ) -> None:

        locations = self._uniform_locations
        functions.glUniform2f(locations["u_center_px"], float(draw_state.center_x_px), float(draw_state.center_y_px))
        functions.glUniform1f(locations["u_radius_px"], float(draw_state.radius_px))
        functions.glUniform1f(locations["u_opacity"], float(draw_state.opacity))
        functions.glUniform3f(locations["u_sun_dir_moon"], *[float(value) for value in draw_state.sun_direction_moon_local])
        functions.glUniform3f(locations["u_earth_dir_moon"], *[float(value) for value in draw_state.earth_direction_moon_local])
        functions.glUniform3f(locations["u_view_to_moon_row0"], *[float(value) for value in draw_state.view_to_moon_matrix[0]])
        functions.glUniform3f(locations["u_view_to_moon_row1"], *[float(value) for value in draw_state.view_to_moon_matrix[1]])
        functions.glUniform3f(locations["u_view_to_moon_row2"], *[float(value) for value in draw_state.view_to_moon_matrix[2]])
        functions.glUniform1i(locations["u_albedo_texture"], 0)
        functions.glUniform1i(locations["u_moon_normal_texture"], 1)
        functions.glUniform1i(locations["u_has_moon_normal_map"], 1 if has_normal_map else 0)
        functions.glUniform1i(locations["u_has_global_moon_normal_map"], 1 if has_global_normal_map else 0)
        functions.glUniform1f(locations["u_terrain_normal_strength"], float(draw_state.terrain_normal_strength if has_normal_map else 0.0))
        functions.glUniform1f(locations["u_terrain_normal_strength_global"], float(draw_state.terrain_normal_strength_global if has_normal_map else 0.0))
        functions.glUniform1f(locations["u_terrain_normal_strength_terminator"], float(draw_state.terrain_normal_strength_terminator if has_normal_map else 0.0))
        functions.glUniform1f(locations["u_tiled_normal_strength"], float(draw_state.tiled_normal_strength if has_normal_map else 0.0))
        functions.glUniform1f(locations["u_tiled_terminator_normal_strength"], float(draw_state.tiled_terminator_normal_strength if has_normal_map else 0.0))
        functions.glUniform1f(locations["u_tiled_normal_fade_factor"], float(draw_state.tiled_normal_fade_factor if has_normal_map else 0.0))
        functions.glUniform1f(locations["u_tiled_normal_max_slope"], float(draw_state.tiled_normal_max_slope))
        functions.glUniform1f(locations["u_earthshine_strength"], float(draw_state.earthshine_strength))
        functions.glUniform1f(locations["u_earthshine_floor"], float(draw_state.earthshine_floor))
        functions.glUniform1f(locations["u_atmosphere_extinction"], float(draw_state.atmosphere_extinction))
        functions.glUniform3f(locations["u_atmosphere_tint_rgb"], *[float(value) for value in draw_state.atmosphere_tint_rgb])
        functions.glUniform1i(locations["u_debug_render_mode"], self.debug_render_mode_code(draw_state.debug_render_mode))
        functions.glUniform1i(locations["u_disable_tone_mapping"], 1 if draw_state.disable_tone_mapping else 0)
        functions.glUniform1i(locations["u_force_tiled_texture_only"], 1 if force_tiled_texture_only else 0)
        self._set_tile_uniform_values(functions, tuple(tile_bindings[: self.MOON_TILE_SAMPLER_LIMIT]), tile_edge_fade_fraction=tile_edge_fade_fraction)
        self._set_polar_cap_uniform_values(functions, draw_state, polar_cap_binding)

    def _set_tile_uniform_values(
        self,
        functions: QOpenGLFunctions_2_0,
        tile_bindings: Sequence[MoonTileShaderBinding],
        *,
        tile_edge_fade_fraction: float,
    ) -> None:

        locations = self._uniform_locations
        tile_count = min(self.MOON_TILE_SAMPLER_LIMIT, len(tile_bindings))
        functions.glUniform1i(locations["u_moonTileAlbedoCount"], tile_count)
        functions.glUniform1f(locations["u_moonTileEdgeFade"], max(0.0, float(tile_edge_fade_fraction)))
        for tile_index in range(self.MOON_TILE_SAMPLER_LIMIT):
            albedo_sampler_name = f"u_moonTileAlbedo{tile_index}"
            normal_sampler_name = f"u_moonTileNormal{tile_index}"
            bounds_name = f"u_moonTileBounds{tile_index}"
            normal_enabled_name = f"u_moonTileNormalEnabled{tile_index}"
            functions.glUniform1i(locations[albedo_sampler_name], 2 + tile_index)
            functions.glUniform1i(locations[normal_sampler_name], 2 + self.MOON_TILE_SAMPLER_LIMIT + tile_index)
            if tile_index < tile_count:
                bounds = tile_bindings[tile_index].uv_bounds
                functions.glUniform4f(locations[bounds_name], float(bounds.u_min), float(bounds.v_min), float(bounds.u_max), float(bounds.v_max))
                functions.glUniform1i(locations[normal_enabled_name], 1 if tile_bindings[tile_index].normal_tile is not None else 0)
            else:
                functions.glUniform4f(locations[bounds_name], 0.0, 0.0, 0.0, 0.0)
                functions.glUniform1i(locations[normal_enabled_name], 0)

    def _set_polar_cap_uniform_values(
        self,
        functions: QOpenGLFunctions_2_0,
        draw_state: MoonDrawState,
        binding: MoonPolarCapBinding | None,
    ) -> None:

        locations = self._uniform_locations
        base_unit = 2 + self.MOON_TILE_SAMPLER_LIMIT * 2
        functions.glUniform1i(locations["u_moonPolarCapNorthAlbedo"], base_unit)
        functions.glUniform1i(locations["u_moonPolarCapSouthAlbedo"], base_unit + 1)
        functions.glUniform1i(locations["u_moonPolarCapNorthNormal"], base_unit + 2)
        functions.glUniform1i(locations["u_moonPolarCapSouthNormal"], base_unit + 3)
        functions.glUniform1i(locations["u_moonPolarCapNorthAlbedoEnabled"], 1 if binding is not None and binding.north_albedo is not None else 0)
        functions.glUniform1i(locations["u_moonPolarCapSouthAlbedoEnabled"], 1 if binding is not None and binding.south_albedo is not None else 0)
        functions.glUniform1i(locations["u_moonPolarCapNorthNormalEnabled"], 1 if binding is not None and binding.north_normal is not None else 0)
        functions.glUniform1i(locations["u_moonPolarCapSouthNormalEnabled"], 1 if binding is not None and binding.south_normal is not None else 0)
        functions.glUniform1f(locations["u_polarCapNormalStrength"], float(draw_state.polar_cap_normal_strength))
        functions.glUniform1f(
            locations["u_polarCapBlendStartLatDeg"],
            float(draw_state.polar_cap_blend_start_lat_deg),
        )
        functions.glUniform1f(
            locations["u_polarCapBlendEndLatDeg"],
            float(draw_state.polar_cap_blend_end_lat_deg),
        )
        functions.glUniform1f(
            locations["u_polarCapBlendCurvePower"],
            float(draw_state.polar_cap_blend_curve_power),
        )
        functions.glUniform1f(
            locations["u_polarCapNormalBlendStartLatDeg"],
            float(draw_state.polar_cap_normal_blend_start_lat_deg),
        )
        functions.glUniform1f(
            locations["u_polarCapNormalBlendEndLatDeg"],
            float(draw_state.polar_cap_normal_blend_end_lat_deg),
        )
        functions.glUniform1f(
            locations["u_polarCapLatitudeCoverageDeg"],
            float(DEFAULT_MOON_VISUAL_SETTINGS.polar_cap_latitude_coverage_deg if binding is None else binding.latitude_coverage_deg),
        )
        functions.glUniform1f(locations["u_polarCapTextureSizePx"], float(max(1, 0 if binding is None else int(binding.texture_size_px))))
        functions.glUniform1f(locations["u_polarCapAlbedoSharpness"], float(draw_state.polar_cap_albedo_sharpness))
        functions.glUniform1f(locations["u_polarCapLocalContrast"], float(draw_state.polar_cap_local_contrast))
        functions.glUniform1f(locations["u_polarCapBoundaryMatchStrength"], float(draw_state.polar_cap_boundary_match_strength))
        functions.glUniform1f(locations["u_polarCapCoreMatchStrength"], float(draw_state.polar_cap_core_match_strength))

    @staticmethod
    def terrain_normal_blend_weight(
        n_dot_l: float,
        terrain_normal_strength: float,
        *,
        settings: MoonVisualSettings = DEFAULT_MOON_VISUAL_SETTINGS,
    ) -> float:

        def smoothstep(edge0: float, edge1: float, value: float) -> float:

            t = max(0.0, min(1.0, (float(value) - edge0) / max(1.0e-9, edge1 - edge0)))
            return t * t * (3.0 - 2.0 * t)

        terminator_weight = 1.0 - smoothstep(
            settings.terminator_band_width,
            settings.terminator_band_width + settings.terminator_softness,
            abs(float(n_dot_l)),
        )
        return max(0.0, min(settings.terrain_blend_cap, max(0.0, float(terrain_normal_strength)) * terminator_weight))

    @staticmethod
    def resolved_terrain_normal_strength_components(
        terrain_normal_strength_global: float,
        terrain_normal_strength_terminator: float,
        diameter_px: float,
        *,
        settings: MoonVisualSettings = DEFAULT_MOON_VISUAL_SETTINGS,
    ) -> tuple[float, float]:

        base_global = max(0.0, float(terrain_normal_strength_global))
        base_terminator = max(0.0, float(terrain_normal_strength_terminator))
        if base_global <= 0.0 and base_terminator <= 0.0:
            return 0.0, 0.0
        diameter = max(0.0, float(diameter_px))
        zoom_t = max(
            0.0,
            min(
                1.0,
                (diameter - settings.terrain_high_zoom_boost_start_px)
                / max(1.0e-6, settings.terrain_high_zoom_boost_end_px - settings.terrain_high_zoom_boost_start_px),
            ),
        )
        boost = 1.0 + settings.terrain_high_zoom_boost_factor * zoom_t
        return min(1.0, base_global * boost), min(1.0, base_terminator * boost)

    @staticmethod
    def resolved_terrain_normal_strength(
        terrain_normal_strength: float,
        diameter_px: float,
        *,
        settings: MoonVisualSettings = DEFAULT_MOON_VISUAL_SETTINGS,
    ) -> float:

        _, resolved_terminator = MoonRendererGL.resolved_terrain_normal_strength_components(
            0.0,
            terrain_normal_strength,
            diameter_px,
            settings=settings,
        )
        return resolved_terminator

    @staticmethod
    def tiled_normal_fade_factor(
        diameter_px: float,
        activation_diameter_px: float = MoonTileSettings().activation_diameter_px,
        *,
        settings: MoonVisualSettings = DEFAULT_MOON_VISUAL_SETTINGS,
    ) -> float:

        fade_start = max(0.0, float(activation_diameter_px)) + max(0.0, float(settings.tiled_normal_fade_start_offset_px))
        fade_end = max(fade_start + 1.0e-6, max(0.0, float(activation_diameter_px)) + max(0.0, float(settings.tiled_normal_fade_end_offset_px)))
        t = max(0.0, min(1.0, (max(0.0, float(diameter_px)) - fade_start) / (fade_end - fade_start)))
        return t * t * (3.0 - 2.0 * t)

    @staticmethod
    def terminator_normal_factor(
        n_dot_l: float,
        *,
        settings: MoonVisualSettings = DEFAULT_MOON_VISUAL_SETTINGS,
    ) -> float:

        t = max(
            0.0,
            min(
                1.0,
                (float(n_dot_l) - settings.terminator_normal_low_light_start)
                / max(1.0e-9, settings.terminator_normal_low_light_end - settings.terminator_normal_low_light_start),
            ),
        )
        smooth_t = t * t * (3.0 - 2.0 * t)
        return settings.terminator_normal_min_factor + (1.0 - settings.terminator_normal_min_factor) * smooth_t

    @staticmethod
    def pole_proximity_factor(
        texture_v: float,
        *,
        start_lat_deg: float = DEFAULT_MOON_VISUAL_SETTINGS.polar_normal_fade_start_lat_deg,
        end_lat_deg: float = DEFAULT_MOON_VISUAL_SETTINGS.polar_normal_fade_end_lat_deg,
    ) -> float:

        absolute_latitude_deg = abs((0.5 - max(0.0, min(1.0, float(texture_v)))) * 180.0)
        t = max(0.0, min(1.0, (absolute_latitude_deg - float(start_lat_deg)) / max(1.0e-9, float(end_lat_deg) - float(start_lat_deg))))
        return t * t * (3.0 - 2.0 * t)

    @staticmethod
    def polar_normal_fade_factor_for_uv(
        texture_v: float,
        *,
        settings: MoonVisualSettings = DEFAULT_MOON_VISUAL_SETTINGS,
    ) -> float:

        return MoonRendererGL.pole_proximity_factor(
            texture_v,
            start_lat_deg=settings.polar_normal_fade_start_lat_deg,
            end_lat_deg=settings.polar_normal_fade_end_lat_deg,
        )

    @staticmethod
    def polar_albedo_fade_factor_for_uv(
        texture_v: float,
        *,
        settings: MoonVisualSettings = DEFAULT_MOON_VISUAL_SETTINGS,
    ) -> float:

        return MoonRendererGL.pole_proximity_factor(
            texture_v,
            start_lat_deg=settings.polar_albedo_fade_start_lat_deg,
            end_lat_deg=settings.polar_albedo_fade_end_lat_deg,
        )

    @staticmethod
    def polar_analytic_normal_active_for_uv(
        texture_v: float,
        *,
        settings: MoonVisualSettings = DEFAULT_MOON_VISUAL_SETTINGS,
    ) -> bool:

        return bool(settings.polar_use_analytic_normal and MoonRendererGL.polar_normal_fade_factor_for_uv(texture_v, settings=settings) >= 1.0 - 1.0e-6)

    @staticmethod
    def polar_cap_blend_factor_for_latitude(
        latitude_deg: float,
        *,
        settings: MoonVisualSettings = DEFAULT_MOON_VISUAL_SETTINGS,
        start_lat_deg: float | None = None,
        end_lat_deg: float | None = None,
        curve_power: float | None = None,
    ) -> float:

        absolute_latitude = abs(float(latitude_deg))
        start = settings.polar_cap_blend_start_lat_deg if start_lat_deg is None else float(start_lat_deg)
        end = settings.polar_cap_blend_end_lat_deg if end_lat_deg is None else float(end_lat_deg)
        t = max(
            0.0,
            min(
                1.0,
                (absolute_latitude - start)
                / max(1.0e-9, end - start),
            ),
        )
        smoother = t * t * t * (t * (t * 6.0 - 15.0) + 10.0)
        return math.pow(smoother, max(0.1, settings.polar_cap_blend_curve_power if curve_power is None else float(curve_power)))

    @staticmethod
    def polar_cap_normal_blend_factor_for_latitude(
        latitude_deg: float,
        *,
        settings: MoonVisualSettings = DEFAULT_MOON_VISUAL_SETTINGS,
        start_lat_deg: float | None = None,
        end_lat_deg: float | None = None,
    ) -> float:

        absolute_latitude = abs(float(latitude_deg))
        start = settings.polar_cap_normal_blend_start_lat_deg if start_lat_deg is None else float(start_lat_deg)
        end = settings.polar_cap_normal_blend_end_lat_deg if end_lat_deg is None else float(end_lat_deg)
        t = max(0.0, min(1.0, (absolute_latitude - start) / max(1.0e-9, end - start)))
        return t * t * t * (t * (t * 6.0 - 15.0) + 10.0)

    @staticmethod
    def polar_cap_uv_for_normal(
        normal_moon: Vector3,
        *,
        settings: MoonVisualSettings = DEFAULT_MOON_VISUAL_SETTINGS,
    ) -> tuple[float, float]:

        resolved_length = math.sqrt(sum(float(component) * float(component) for component in normal_moon))
        if resolved_length <= 1.0e-12:
            return (0.5, 0.5)
        normal = tuple(float(component) / resolved_length for component in normal_moon)
        absolute_latitude = math.degrees(math.asin(max(-1.0, min(1.0, abs(normal[1])))))
        radius = (90.0 - absolute_latitude) / max(1.0e-9, 90.0 - settings.polar_cap_latitude_coverage_deg)
        horizontal = math.sqrt(normal[0] * normal[0] + normal[2] * normal[2])
        direction = (0.0, -1.0) if horizontal <= 1.0e-12 else (normal[0] / horizontal, -normal[2] / horizontal)
        return (
            max(0.0, min(1.0, 0.5 + direction[0] * radius * 0.5)),
            max(0.0, min(1.0, 0.5 + direction[1] * radius * 0.5)),
        )

    @staticmethod
    def seam_proximity_factor(
        texture_u: float,
        *,
        settings: MoonVisualSettings = DEFAULT_MOON_VISUAL_SETTINGS,
    ) -> float:

        wrapped_u = float(texture_u) % 1.0
        seam_distance = min(wrapped_u, 1.0 - wrapped_u)
        t = max(0.0, min(1.0, seam_distance / max(1.0e-9, settings.seam_diagnostic_width_uv)))
        smooth_t = t * t * (3.0 - 2.0 * t)
        return 1.0 - smooth_t

    @staticmethod
    def clamped_tiled_normal_slope(normal: Vector3, max_slope: float) -> Vector3:

        normal_length = math.sqrt(sum(float(component) * float(component) for component in normal))
        if normal_length <= 1.0e-12:
            return (0.0, 0.0, 1.0)
        resolved = tuple(float(component) / normal_length for component in normal)
        tangent_length = math.sqrt(resolved[0] * resolved[0] + resolved[1] * resolved[1])
        allowed_tangent = max(0.0, float(max_slope)) * max(resolved[2], 1.0e-4)
        if tangent_length > allowed_tangent and tangent_length > 1.0e-12:
            scale = allowed_tangent / tangent_length
            resolved = (resolved[0] * scale, resolved[1] * scale, resolved[2])
        final_length = math.sqrt(sum(component * component for component in resolved))
        return tuple(component / max(final_length, 1.0e-12) for component in resolved)  # type: ignore[return-value]

    @staticmethod
    def highlight_compression_value(value: float, *, settings: MoonVisualSettings = DEFAULT_MOON_VISUAL_SETTINGS) -> float:

        resolved_value = max(0.0, float(value)) * settings.tone_exposure
        if resolved_value <= settings.tone_highlight_knee:
            return resolved_value
        excess = resolved_value - settings.tone_highlight_knee
        return settings.tone_highlight_knee + excess / (1.0 + excess * settings.tone_highlight_compression)

    @staticmethod
    def lighting_scalar_value(
        n_dot_l: float,
        earthshine: float = 0.0,
        *,
        settings: MoonVisualSettings = DEFAULT_MOON_VISUAL_SETTINGS,
    ) -> float:

        def smoothstep(edge0: float, edge1: float, value: float) -> float:

            t = max(0.0, min(1.0, (float(value) - edge0) / max(1.0e-9, edge1 - edge0)))
            return t * t * (3.0 - 2.0 * t)

        soft_light = smoothstep(settings.terminator_soft_start, settings.terminator_soft_end, n_dot_l)
        hemispheric_light = math.pow(max(0.0, min(1.0, float(n_dot_l))), settings.lighting_hemisphere_gamma)
        direct_light = max(0.0, min(1.0, (1.0 - settings.lighting_hemisphere_blend) * soft_light + settings.lighting_hemisphere_blend * hemispheric_light))
        lighting_value = max(0.0, float(earthshine) + direct_light * settings.direct_light_gain)
        if lighting_value <= settings.lighting_highlight_knee:
            return min(1.0, lighting_value)
        excess = lighting_value - settings.lighting_highlight_knee
        return max(
            0.0,
            min(1.0, settings.lighting_highlight_knee + excess / (1.0 + excess * settings.lighting_highlight_compression)),
        )

    @staticmethod
    def albedo_remapped_color(color: Vector3, *, settings: MoonVisualSettings = DEFAULT_MOON_VISUAL_SETTINGS) -> Vector3:

        brightened = tuple(max(0.0, min(1.5, float(component) * settings.albedo_brightness)) for component in color)
        contrasted = tuple(max(0.0, min(1.25, 0.5 + (component - 0.5) * settings.albedo_contrast)) for component in brightened)
        luminance = 0.2126 * contrasted[0] + 0.7152 * contrasted[1] + 0.0722 * contrasted[2]
        separation_weight = max(0.0, min(1.0, (luminance - 0.12) / 0.76))
        remapped_luminance = max(
            0.0,
            min(1.0, luminance + (luminance - 0.46) * settings.mare_highland_contrast_boost * separation_weight),
        )
        return tuple(
            max(0.0, min(1.0, remapped_luminance + (component - luminance) * settings.albedo_saturation_retention))
            for component in contrasted
        )  # type: ignore[return-value]

    @staticmethod
    def debug_render_mode_code(mode: str) -> int:

        return {
            "normal": 0,
            "albedo_only": 1,
            "lighting_only": 2,
            "normal_visualization": 3,
            "uv_debug": 4,
            "tile_source_route": 5,
            "global_tiled_difference": 6,
            "polar_cap_route": 7,
            "polar_cap_footprint": 8,
            "synthetic_global_uv_grid": 1,
            "synthetic_tiled_uv_grid": 1,
        }.get(str(mode or "normal").strip().casefold(), 0)

    @classmethod
    def tone_mapped_color(cls, color: Vector3, *, settings: MoonVisualSettings = DEFAULT_MOON_VISUAL_SETTINGS) -> Vector3:

        compressed = tuple(cls.highlight_compression_value(component, settings=settings) for component in color)
        contrasted = tuple(
            max(0.0, min(1.0, settings.tone_pivot + (component - settings.tone_pivot) * settings.tone_contrast))
            for component in compressed
        )
        luminance = 0.2126 * contrasted[0] + 0.7152 * contrasted[1] + 0.0722 * contrasted[2]
        midtone_weight = max(0.0, min(1.0, (luminance - 0.08) / 0.22)) * max(0.0, min(1.0, (0.95 - luminance) / 0.23))
        chroma_gain = settings.tone_saturation + settings.tone_color_preservation * midtone_weight
        saturated = tuple(
            max(0.0, min(1.0, luminance + (component - luminance) * chroma_gain)) for component in contrasted
        )
        gamma = max(1.0e-6, settings.tone_gamma)
        return tuple(max(0.0, min(1.0, math.pow(max(0.0, component), gamma))) for component in saturated)  # type: ignore[return-value]

    @staticmethod
    def tile_sample_route_for_uv(
        texture_u: float,
        texture_v: float,
        tile_bindings: Sequence[MoonTileShaderBinding],
        *,
        tile_edge_fade_fraction: float = MoonTileSettings().tile_edge_fade_fraction,
    ) -> MoonTileSampleRoute:

        uv_u = float(texture_u) % 1.0
        uv_v = max(0.0, min(1.0, float(texture_v)))
        for binding in tuple(tile_bindings[: MoonRendererGL.MOON_TILE_SAMPLER_LIMIT]):
            bounds = binding.uv_bounds
            contains_u = bounds.u_min <= bounds.u_max and bounds.u_min <= uv_u <= bounds.u_max
            if bounds.u_min > bounds.u_max:
                contains_u = uv_u >= bounds.u_min or uv_u <= bounds.u_max
            if contains_u and bounds.v_min <= uv_v <= bounds.v_max:
                sample_u = uv_u
                span_u = bounds.u_max - bounds.u_min
                if span_u < 0.0:
                    span_u += 1.0
                    if sample_u < bounds.u_min:
                        sample_u += 1.0
                span_u = max(1.0e-9, span_u)
                span_v = max(1.0e-9, bounds.v_max - bounds.v_min)
                local_u = (sample_u - bounds.u_min) / span_u
                local_v = (uv_v - bounds.v_min) / span_v
                edge = min(local_u, 1.0 - local_u, local_v, 1.0 - local_v)
                fade = max(1.0e-9, float(tile_edge_fade_fraction))
                edge_t = max(0.0, min(1.0, edge / fade))
                edge_weight = edge_t * edge_t * (3.0 - 2.0 * edge_t)
                return MoonTileSampleRoute(
                    source="tile",
                    tile_key=MoonTiledTextureManager._format_tile_key(binding.key),
                    local_u=local_u,
                    local_v=local_v,
                    edge_weight=edge_weight,
                )
        return MoonTileSampleRoute(source="global")

    @staticmethod
    def protected_cache_keys_for_draw(
        texture_lod: MoonTextureLod,
        normal_texture_lod: MoonTextureLod | None,
        tile_bindings: Sequence[MoonTileShaderBinding],
        polar_cap_binding: MoonPolarCapBinding | None = None,
    ) -> set[tuple[object, ...]]:

        protected_cache_keys = {tuple(texture_lod.cache_key)}
        if normal_texture_lod is not None:
            protected_cache_keys.add(tuple(normal_texture_lod.cache_key))
        for binding in tuple(tile_bindings[: MoonRendererGL.MOON_TILE_SAMPLER_LIMIT]):
            protected_cache_keys.add(tuple(binding.albedo_tile.cache_key))
            if binding.normal_tile is not None:
                protected_cache_keys.add(tuple(binding.normal_tile.cache_key))
        if polar_cap_binding is not None:
            for cap_lod in (
                polar_cap_binding.north_albedo,
                polar_cap_binding.south_albedo,
                polar_cap_binding.north_normal,
                polar_cap_binding.south_normal,
            ):
                if cap_lod is not None:
                    protected_cache_keys.add(tuple(cap_lod.cache_key))
        return protected_cache_keys

    @classmethod
    def _texture_unit(cls, unit_index: int) -> int:

        return cls._GL_TEXTURE0 + int(unit_index)

    def _draw_disc_quad(self, functions: QOpenGLFunctions_2_0, draw_state: MoonDrawState) -> None:

        radius = max(1.0, float(draw_state.radius_px) + 2.0)
        left = max(0.0, draw_state.center_x_px - radius)
        right = min(float(draw_state.viewport_width_px), draw_state.center_x_px + radius)
        bottom = max(0.0, draw_state.center_y_px - radius)
        top = min(float(draw_state.viewport_height_px), draw_state.center_y_px + radius)
        if right <= left or top <= bottom:
            return
        clip_left = (left / float(draw_state.viewport_width_px)) * 2.0 - 1.0
        clip_right = (right / float(draw_state.viewport_width_px)) * 2.0 - 1.0
        clip_bottom = (bottom / float(draw_state.viewport_height_px)) * 2.0 - 1.0
        clip_top = (top / float(draw_state.viewport_height_px)) * 2.0 - 1.0
        began = False
        try:
            functions.glBegin(self._GL_QUADS)
            began = True
            functions.glVertex2f(float(clip_left), float(clip_bottom))
            functions.glVertex2f(float(clip_right), float(clip_bottom))
            functions.glVertex2f(float(clip_right), float(clip_top))
            functions.glVertex2f(float(clip_left), float(clip_top))
        finally:
            if began:
                functions.glEnd()

    def _ensure_texture(
        self,
        texture_lod: MoonTextureLod | MoonTextureTile,
        *,
        cache_budget_bytes: int,
        protected_cache_keys: set[tuple[object, ...]] | None = None,
    ) -> _MoonTextureCacheEntry:

        benchmark_recorder = get_benchmark_recorder() if BENCHMARK_ENABLED else None

        benchmark_token = benchmark_recorder.start_section(

            "moon.texture_cache",

            metadata={"label": getattr(texture_lod, "label", "unknown")},

        ) if benchmark_recorder is not None else None

        cache_key = tuple(texture_lod.cache_key)
        protected = set(protected_cache_keys or set())
        protected.add(cache_key)
        cached_entry = self._textures.get(cache_key)
        if cached_entry is not None and cached_entry.texture.isCreated():
            self._textures.move_to_end(cache_key)
            self._evict_textures_to_budget(cache_budget_bytes, protected_cache_keys=protected)
            if benchmark_recorder is not None:
                benchmark_recorder.stop_section(
                    benchmark_token,
                    metadata={"cache_status": "hit", "width": cached_entry.width, "height": cached_entry.height},
                )
            return cached_entry
        if cached_entry is not None:
            self._release_texture(cached_entry.texture)
            self._texture_cache_total_bytes = max(0, self._texture_cache_total_bytes - cached_entry.approx_bytes)
            self._textures.pop(cache_key, None)

        upload_start = perf_counter()
        upload_image = QImage(texture_lod.image).mirrored(False, True)
        texture = QOpenGLTexture(upload_image)
        texture.setMagnificationFilter(QOpenGLTexture.Filter.Linear)
        try:
            texture.generateMipMaps()
            texture.setMinificationFilter(QOpenGLTexture.Filter.LinearMipMapLinear)
        except Exception:
            texture.setMinificationFilter(QOpenGLTexture.Filter.Linear)
        wrap_s = QOpenGLTexture.WrapMode.ClampToEdge if cache_key and cache_key[0] == "moon-polar-cap" else QOpenGLTexture.WrapMode.Repeat
        texture.setWrapMode(QOpenGLTexture.CoordinateDirection.DirectionS, wrap_s)
        texture.setWrapMode(QOpenGLTexture.CoordinateDirection.DirectionT, QOpenGLTexture.WrapMode.ClampToEdge)
        self.last_texture_upload_seconds = perf_counter() - upload_start
        self.last_texture_uploaded = True
        entry = _MoonTextureCacheEntry(
            texture=texture,
            width=int(upload_image.width()),
            height=int(upload_image.height()),
            approx_bytes=max(1, int(upload_image.width()) * int(upload_image.height()) * 4),
        )
        self._textures[cache_key] = entry
        self._textures.move_to_end(cache_key)
        self._texture_cache_total_bytes += entry.approx_bytes
        self._evict_textures_to_budget(cache_budget_bytes, protected_cache_keys=protected)
        if benchmark_recorder is not None:
            benchmark_recorder.stop_section(
                benchmark_token,
                metadata={"cache_status": "miss", "width": entry.width, "height": entry.height, "approx_bytes": entry.approx_bytes},
            )
        return entry

    def _evict_textures_to_budget(self, cache_budget_bytes: int, *, protected_cache_keys: set[tuple[object, ...]] | None = None) -> None:

        protected = protected_cache_keys or set()
        resolved_budget = max(1, int(cache_budget_bytes))
        while self._textures and self._texture_cache_total_bytes > resolved_budget:
            evict_key = None
            for candidate_key in self._textures.keys():
                if candidate_key not in protected:
                    evict_key = candidate_key
                    break
            if evict_key is None:
                break
            entry = self._textures.pop(evict_key)
            self._texture_cache_total_bytes = max(0, self._texture_cache_total_bytes - entry.approx_bytes)
            self._release_texture(entry.texture)

    def _destroy_textures(self) -> None:

        for entry in list(self._textures.values()):
            self._release_texture(entry.texture)
        self._textures.clear()
        self._texture_cache_total_bytes = 0

    @staticmethod
    def _release_texture(texture: QOpenGLTexture) -> None:

        try:
            texture.release()
        except Exception:
            pass
        try:
            texture.destroy()
        except Exception:
            pass

    @staticmethod
    def _integer_values(functions: QOpenGLFunctions_2_0, name: int, count: int) -> tuple[int, ...] | None:

        try:
            values = [0 for _ in range(count)]
            functions.glGetIntegerv(int(name), values)
            return tuple(int(value) for value in values)
        except Exception:
            return None

    @staticmethod
    def _is_enabled(functions: QOpenGLFunctions_2_0, capability: int) -> bool:

        try:
            return bool(functions.glIsEnabled(int(capability)))
        except Exception:
            return False

    @staticmethod
    def _restore_enabled_states(functions: QOpenGLFunctions_2_0, states: dict[int, bool]) -> None:

        for capability, was_enabled in states.items():
            if was_enabled:
                functions.glEnable(capability)
            else:
                functions.glDisable(capability)

    def _check_gl_error(self, functions: QOpenGLFunctions_2_0, context: str) -> None:

        try:
            error_code = int(functions.glGetError())
        except Exception:
            return
        if error_code != self._GL_NO_ERROR:
            raise RuntimeError(f"OpenGL error {error_code} {context}")


DEFAULT_MOON_VALIDATION_CASES: tuple[MoonEphemerisValidationCase, ...] = (
    MoonEphemerisValidationCase("Greenwich first quarter", datetime(2026, 5, 23, 21, 0, tzinfo=UTC), 51.4769, 0.0, 46.0),
    MoonEphemerisValidationCase("Texas crescent", datetime(2026, 5, 18, 3, 0, tzinfo=UTC), 31.5475, -99.3819, 472.0),
    MoonEphemerisValidationCase("Tokyo full moon", datetime(2026, 5, 31, 12, 0, tzinfo=UTC), 35.6895, 139.6917, 44.0),
    MoonEphemerisValidationCase("Sydney horizon moon", datetime(2026, 5, 25, 9, 0, tzinfo=UTC), -33.8688, 151.2093, 58.0),
)


def validate_moon_ephemeris_against_astropy(
    cases: Sequence[MoonEphemerisValidationCase] = DEFAULT_MOON_VALIDATION_CASES,
    *,
    sample_interval_seconds: int = 300,
) -> tuple[MoonEphemerisValidationResult, ...]:

    cache = MoonCache()
    ephemeris = MoonEphemeris(cache, sample_interval_seconds=sample_interval_seconds)
    results: list[MoonEphemerisValidationResult] = []
    for case in cases:
        cached_state = ephemeris.state_at(case.observation_time_utc, case.latitude_deg, case.longitude_deg, case.elevation_m)
        precise_state = ephemeris.precise_state_at(case.observation_time_utc, case.latitude_deg, case.longitude_deg, case.elevation_m)
        results.append(
            MoonEphemerisValidationResult(
                case=case,
                altitude_error_arcsec=abs(cached_state.altitude_deg - precise_state.altitude_deg) * 3600.0,
                azimuth_error_arcsec=MoonEphemeris._angle_distance_deg(cached_state.azimuth_deg, precise_state.azimuth_deg) * 3600.0,
                angular_diameter_error_arcsec=abs(cached_state.angular_diameter_deg - precise_state.angular_diameter_deg) * 3600.0,
                illuminated_fraction_error=abs(cached_state.illuminated_fraction - precise_state.illuminated_fraction),
            )
        )
    cache.shutdown()
    return tuple(results)


@dataclass(frozen=True, slots=True)
class MoonVisualSmokeCase:

    label: str
    observation_time_utc: datetime
    latitude_deg: float
    longitude_deg: float
    field_width_deg: float
    expected_phase: str


DEFAULT_MOON_VISUAL_SMOKE_CASES: tuple[MoonVisualSmokeCase, ...] = (
    MoonVisualSmokeCase("new moon", datetime(2026, 5, 16, 20, 0, tzinfo=UTC), 31.5475, -99.3819, 65.0, "new"),
    MoonVisualSmokeCase("crescent", datetime(2026, 5, 19, 2, 30, tzinfo=UTC), 31.5475, -99.3819, 45.0, "crescent"),
    MoonVisualSmokeCase("first quarter", datetime(2026, 5, 23, 21, 0, tzinfo=UTC), 51.4769, 0.0, 35.0, "first-quarter"),
    MoonVisualSmokeCase("full moon", datetime(2026, 5, 31, 12, 0, tzinfo=UTC), 35.6895, 139.6917, 30.0, "full"),
    MoonVisualSmokeCase("horizon moon", datetime(2026, 5, 25, 9, 0, tzinfo=UTC), -33.8688, 151.2093, 50.0, "horizon"),
    MoonVisualSmokeCase("high zoom moon", datetime(2026, 5, 23, 21, 0, tzinfo=UTC), 31.5475, -99.3819, 1.2, "detail"),
    MoonVisualSmokeCase("fast time-scrub", datetime(2026, 5, 20, 0, 0, tzinfo=UTC), 31.5475, -99.3819, 25.0, "scrub"),
    MoonVisualSmokeCase("thin crescent earthshine", datetime(2026, 5, 18, 2, 30, tzinfo=UTC), 31.5475, -99.3819, 28.0, "thin-crescent-earthshine"),
    MoonVisualSmokeCase("first quarter crater terminator", datetime(2026, 5, 23, 21, 0, tzinfo=UTC), 51.4769, 0.0, 12.0, "terminator-detail"),
    MoonVisualSmokeCase("full moon low terrain contrast", datetime(2026, 5, 31, 12, 0, tzinfo=UTC), 35.6895, 139.6917, 10.0, "full-low-terrain"),
    MoonVisualSmokeCase("near horizon reddened moon", datetime(2026, 5, 25, 9, 0, tzinfo=UTC), -33.8688, 151.2093, 18.0, "horizon-atmosphere"),
    MoonVisualSmokeCase("high zoom normal map moon", datetime(2026, 5, 23, 21, 0, tzinfo=UTC), 31.5475, -99.3819, 0.8, "normal-detail"),
    MoonVisualSmokeCase("high zoom full moon", datetime(2026, 5, 31, 12, 0, tzinfo=UTC), 35.6895, 139.6917, 0.45, "high-zoom-full"),
    MoonVisualSmokeCase("high zoom first quarter", datetime(2026, 5, 23, 21, 0, tzinfo=UTC), 51.4769, 0.0, 0.45, "high-zoom-first-quarter"),
    MoonVisualSmokeCase("high zoom crescent", datetime(2026, 5, 19, 2, 30, tzinfo=UTC), 31.5475, -99.3819, 0.45, "high-zoom-crescent"),
    MoonVisualSmokeCase("high zoom missing tiles", datetime(2026, 5, 23, 21, 0, tzinfo=UTC), 31.5475, -99.3819, 0.42, "high-zoom-missing-tiles"),
    MoonVisualSmokeCase("fast scrub no texture stall", datetime(2026, 5, 20, 0, 0, tzinfo=UTC), 31.5475, -99.3819, 18.0, "scrub-no-stall"),
)
