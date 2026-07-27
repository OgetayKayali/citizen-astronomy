"""Moon illumination and Krisciunas–Schaefer moonlight-impact helpers.

Informational only: Deep Stack continues to weight frames from measured image
background and noise, not from these lunar estimates.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
import math

from astropy import units as u
from astropy.coordinates import AltAz, EarthLocation, SkyCoord, get_body, get_sun
from astropy.time import Time
from astropy.utils import iers

MEAN_EARTH_MOON_DISTANCE_KM = 384_400.0
DEFAULT_V_BAND_EXTINCTION_MAG = 0.172
DEFAULT_DARK_ZENITH_BRIGHTNESS_NL = 79.0
# Score saturates near the near-full / small-separation Table 2 extremes (~4.5 mag).
_MOONLIGHT_SCORE_AV_SCALE = 4.5


@dataclass(frozen=True, slots=True)
class MoonlightImpactEstimate:
    """Per-frame lunar illumination and scattered-moonlight impact."""

    illumination_percent: float
    moonlight_brightness_nl: float
    relative_score: float
    category: str
    moon_altitude_deg: float
    moon_distance_km: float
    target_altitude_deg: float
    target_moon_separation_deg: float
    phase_angle_deg: float


def moonlight_impact_category(relative_score: float) -> str:
    score = max(0.0, float(relative_score))
    if score < 20.0:
        return "Low"
    if score < 40.0:
        return "Moderate"
    if score < 65.0:
        return "High"
    return "Severe"


def scattering_airmass(zenith_distance_deg: float) -> float:
    """Krisciunas–Schaefer scattering air mass X(Z) = (1 - 0.96 sin^2 Z)^(-0.5)."""

    zenith = max(0.0, min(90.0, float(zenith_distance_deg)))
    sin_z = math.sin(math.radians(zenith))
    denominator = max(1.0e-6, 1.0 - 0.96 * sin_z * sin_z)
    return denominator ** -0.5


def lunar_illuminance_outside_atmosphere(*, phase_angle_deg: float, distance_km: float) -> float:
    """I* from KS eq. (20), with inverse-square Earth–Moon distance correction."""

    alpha = abs(float(phase_angle_deg))
    illuminance = 10.0 ** (-0.4 * (3.84 + 0.026 * alpha + 4.0e-9 * (alpha**4)))
    distance = max(1.0, float(distance_km))
    return illuminance * ((MEAN_EARTH_MOON_DISTANCE_KM / distance) ** 2)


def scattering_function(separation_deg: float) -> float:
    """KS scattering function f(ρ), with the small-angle aureole branch for ρ < 10°."""

    rho = max(0.05, float(separation_deg))
    if rho < 10.0:
        return 6.2e7 / (rho * rho)
    rho_rad = math.radians(rho)
    rayleigh = (10.0**5.36) * (1.06 + math.cos(rho_rad) ** 2)
    mie = 10.0 ** (6.15 - rho / 40.0)
    return rayleigh + mie


def krisciunas_schaefer_moonlight_nl(
    *,
    phase_angle_deg: float,
    moon_altitude_deg: float,
    target_altitude_deg: float,
    separation_deg: float,
    distance_km: float,
    extinction_mag: float = DEFAULT_V_BAND_EXTINCTION_MAG,
) -> float:
    """Estimate scattered lunar sky brightness B_moon in nanoLamberts.

    Returns 0 when the Moon is below the horizon.
    """

    if float(moon_altitude_deg) <= 0.0:
        return 0.0
    moon_zenith = max(0.0, 90.0 - float(moon_altitude_deg))
    target_altitude = max(0.0, float(target_altitude_deg))
    target_zenith = max(0.0, 90.0 - target_altitude)
    k = max(0.0, float(extinction_mag))
    illuminance = lunar_illuminance_outside_atmosphere(
        phase_angle_deg=phase_angle_deg,
        distance_km=distance_km,
    )
    f_rho = scattering_function(separation_deg)
    x_moon = scattering_airmass(moon_zenith)
    x_target = scattering_airmass(target_zenith)
    brightness = (
        f_rho
        * illuminance
        * (10.0 ** (-0.4 * k * x_moon))
        * (1.0 - 10.0 ** (-0.4 * k * x_target))
    )
    return max(0.0, float(brightness))


def relative_moonlight_impact_score(
    moonlight_brightness_nl: float,
    *,
    dark_zenith_brightness_nl: float = DEFAULT_DARK_ZENITH_BRIGHTNESS_NL,
) -> float:
    """Map B_moon to a 0–100 relative impact score via KS ΔV against dark zenith sky."""

    brightness = max(0.0, float(moonlight_brightness_nl))
    if brightness <= 0.0:
        return 0.0
    dark = max(1.0e-6, float(dark_zenith_brightness_nl))
    delta_v = abs(-2.5 * math.log10((brightness + dark) / dark))
    return max(0.0, min(100.0, 100.0 * delta_v / _MOONLIGHT_SCORE_AV_SCALE))


def _normalize_utc(observation_time: datetime) -> datetime:
    if observation_time.tzinfo is None:
        return observation_time.replace(tzinfo=UTC)
    return observation_time.astimezone(UTC)


def illuminated_fraction_from_phase_angle(phase_angle_deg: float) -> float:
    return max(0.0, min(1.0, 0.5 * (1.0 + math.cos(math.radians(float(phase_angle_deg))))))


def evaluate_moonlight_impact(
    observation_time: datetime,
    *,
    latitude_deg: float,
    longitude_deg: float,
    elevation_m: float = 0.0,
    target_ra_deg: float,
    target_dec_deg: float,
    extinction_mag: float = DEFAULT_V_BAND_EXTINCTION_MAG,
) -> MoonlightImpactEstimate:
    """Compute topocentric Moon geometry and Krisciunas–Schaefer impact for one frame."""

    resolved_time = _normalize_utc(observation_time)
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
        target = SkyCoord(
            ra=float(target_ra_deg) * u.deg,
            dec=float(target_dec_deg) * u.deg,
            frame="icrs",
        )
        target_altaz = target.transform_to(altaz_frame)
        separation_deg = float(moon_altaz.separation(target_altaz).deg)

    moon_vector = moon.cartesian.xyz.to_value(u.km)
    sun_vector = sun.cartesian.xyz.to_value(u.km)
    phase_angle_rad = _angle_between(sun_vector - moon_vector, -moon_vector)
    phase_angle_deg = math.degrees(phase_angle_rad)
    illuminated_fraction = illuminated_fraction_from_phase_angle(phase_angle_deg)
    distance_km = max(1.0, float(moon.distance.to_value(u.km)))
    moon_altitude_deg = float(moon_altaz.alt.deg)
    target_altitude_deg = float(target_altaz.alt.deg)
    brightness_nl = krisciunas_schaefer_moonlight_nl(
        phase_angle_deg=phase_angle_deg,
        moon_altitude_deg=moon_altitude_deg,
        target_altitude_deg=target_altitude_deg,
        separation_deg=separation_deg,
        distance_km=distance_km,
        extinction_mag=extinction_mag,
    )
    score = relative_moonlight_impact_score(brightness_nl)
    return MoonlightImpactEstimate(
        illumination_percent=illuminated_fraction * 100.0,
        moonlight_brightness_nl=brightness_nl,
        relative_score=score,
        category=moonlight_impact_category(score),
        moon_altitude_deg=moon_altitude_deg,
        moon_distance_km=distance_km,
        target_altitude_deg=target_altitude_deg,
        target_moon_separation_deg=separation_deg,
        phase_angle_deg=phase_angle_deg,
    )


def moon_illumination_percent(
    observation_time: datetime,
    *,
    latitude_deg: float | None = None,
    longitude_deg: float | None = None,
    elevation_m: float = 0.0,
) -> float:
    """Return Moon illuminated fraction as a percentage (0–100)."""

    resolved_time = _normalize_utc(observation_time)
    astropy_time = Time(resolved_time, scale="utc")
    location = None
    if latitude_deg is not None and longitude_deg is not None:
        location = EarthLocation(
            lat=float(latitude_deg) * u.deg,
            lon=float(longitude_deg) * u.deg,
            height=float(elevation_m) * u.m,
        )
    with iers.conf.set_temp("auto_max_age", None):
        moon = get_body("moon", astropy_time, location) if location is not None else get_body("moon", astropy_time)
        sun = get_body("sun", astropy_time, location) if location is not None else get_sun(astropy_time)
    moon_vector = moon.cartesian.xyz.to_value(u.km)
    sun_vector = sun.cartesian.xyz.to_value(u.km)
    phase_angle_rad = _angle_between(sun_vector - moon_vector, -moon_vector)
    return illuminated_fraction_from_phase_angle(math.degrees(phase_angle_rad)) * 100.0


def _angle_between(vector_a, vector_b) -> float:
    a = [float(value) for value in vector_a]
    b = [float(value) for value in vector_b]
    norm_a = math.sqrt(sum(value * value for value in a))
    norm_b = math.sqrt(sum(value * value for value in b))
    if norm_a <= 0.0 or norm_b <= 0.0:
        return 0.0
    dot = sum(left * right for left, right in zip(a, b, strict=True)) / (norm_a * norm_b)
    return math.acos(max(-1.0, min(1.0, dot)))
