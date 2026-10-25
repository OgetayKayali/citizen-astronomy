"""CME teaching model. Distances R_E, speed km/s, density cm^-3, field nT.

Shue et al. (1998), JGR 103, 17691, doi:10.1029/98JA01103, eqs. 10–11.
Jelínek et al. (2012), doi:10.1029/2011JA017252, pressure-only bow shock.
Newell et al. (2007), doi:10.1029/2006JA012015, normalized coupling driver.
Boundary equilibria are empirical; deformed lines are not an MHD solution.
See guides/aurora_magnetosphere.md for limitations and temporal conventions.
"""
from __future__ import annotations

from dataclasses import dataclass
from functools import lru_cache
import math

import numpy as np

PROTON_MASS = 1.67262192369e-27
EARTH_RADIUS_KM = 6371.2
TIME_SCALE = 60.0  # physical seconds per timeline second (one displayed minute)
UPSTREAM_X = 40.0
DRIVER_X = 24.0  # fixed unshocked input plane, outside all supported bow noses
EVENT_START = 12.0
PROFILE_X = np.linspace(-84., UPSTREAM_X, 256)


def alfven_mach_upper_bound(speed, density, bz):
    """Using |Bz| <= |B| gives an upper bound on M_A, not a full Mach estimate.

    M_fast <= M_A. A bound <= 1 rules out the assumed superfast bow shock;
    a larger bound cannot establish it without temperature and full IMF.
    """
    b = np.abs(np.asarray(bz))
    va = b*1e-9/np.sqrt(4*np.pi*1e-7*PROTON_MASS*np.asarray(density)*1e6)/1e3
    return np.divide(speed, va, out=np.full(np.broadcast_shapes(np.shape(speed), np.shape(va)), np.inf), where=va > 0)


def dynamic_pressure(density: float, speed: float) -> float:
    """Proton-only rho*v² in nPa (no 1/2 factor or implicit alpha fraction)."""
    if not (math.isfinite(density) and math.isfinite(speed) and density > 0 and speed > 0):
        raise ValueError("Density and speed must be finite and positive")
    return PROTON_MASS * density * 1e6 * (speed * 1e3)**2 * 1e9


def shue_parameters(pressure, bz):
    pressure, bz = np.asarray(pressure), np.asarray(bz)
    if np.any(~np.isfinite(pressure)) or np.any(pressure <= 0) or np.any(~np.isfinite(bz)):
        raise ValueError("Shue requires positive finite nPa and finite Bz in nT")
    return ((10.22 + 1.29*np.tanh(.184*(bz+8.14))) * pressure**(-1/6.6),
            (.58-.007*bz)*(1+.024*np.log(pressure)))


def shue_radius(theta, r0, alpha):
    theta = np.asarray(theta)
    if np.any(~np.isfinite(theta)) or np.any(np.abs(theta) >= np.pi):
        raise ValueError("Shue tail is singular at pi; use a finite viewing domain")
    return r0*(2/(1+np.cos(theta)))**alpha


def jelinek_nose(pressure):
    """Jelínek et al. 2012, doi:10.1029/2011JA017252; nPa -> R_E."""
    p = np.asarray(pressure)
    if np.any(~np.isfinite(p)) or np.any(p <= 0):
        raise ValueError("Bow shock requires positive finite pressure")
    return 15.02*p**(-1/6.55)


def jelinek_rho(x, nose):
    """Generalized-parabola fit: rho²=4 R_BS(R_BS-x)/lambda².

    Derived from sigma²=2 R_BS and the paper's scaled parabolic
    coordinates; lambda=1.17. No Mach-number dependence or aberration.
    The renderer deliberately uses only the sunward half (x >= 0).
    """
    return np.sqrt(np.maximum(0., 4*nose*(nose-np.asarray(x))))/1.17


def newell_driver(speed, bz):
    """Dimensionless Newell (2007) coupling relative to 400 km/s, -5 nT.

    v^(4/3) BT^(2/3) sin(clock/2)^(8/3), By=0. Not Wb/s or watts.
    Zero for northward Bz denotes this dayside proxy, not zero global coupling.
    """
    v, b = np.asarray(speed), np.asarray(bz)
    if np.any(~np.isfinite(v)) or np.any(v <= 0) or np.any(~np.isfinite(b)):
        raise ValueError("Coupling requires finite positive speed and finite Bz")
    return (v/400)**(4/3)*(np.maximum(-b, 0)/5)**(2/3)


@dataclass(frozen=True)
class Parameters:
    speed: float = 800
    density: float = 18
    bz: float = -15
    duration: float = 50  # model minutes at the upstream monitor
    tilt: float = 11
    quiet: bool = False

    def __post_init__(self):
        for name, low, high in (("speed", 250, 1500), ("density", 1, 40),
                                ("bz", -25, 20), ("duration", 20, 120), ("tilt", -30, 30)):
            value = getattr(self, name)
            if not math.isfinite(value) or not low <= value <= high:
                raise ValueError(f"{name} must be in [{low}, {high}]")


PRESETS = {
    "Quiet Solar Wind": Parameters(400, 5, 3, quiet=True),
    "CME — Northward IMF": Parameters(bz=15),
    "CME — Southward IMF": Parameters(bz=-15),
}


def smoothstep(x):
    x = np.clip(x, 0, 1)
    return x*x*(3-2*x)


@dataclass(frozen=True)
class Wind:
    speed: float
    density: float
    bz: float
    envelope: float

    @property
    def pressure(self):
        return dynamic_pressure(self.density, self.speed)


@dataclass(frozen=True)
class AuroralDriver:
    """Uncalibrated handoff contract, deliberately not a precipitation prediction."""
    physical_seconds: float
    near_earth_wind: Wind
    normalized_coupling: float
    tail_storage_proxy: float
    release_proxy_per_minute: float
    dipole_tilt_degrees: float
    energy_flux_mw_m2: float | None = None
    characteristic_energy_kev: float | None = None
    model: str = "uncalibrated magnetosphere teaching proxies; precipitation not modeled"


@dataclass(frozen=True)
class State:
    time: float
    speed: float
    density: float
    bz: float
    pressure: float
    r0: float
    alpha: float
    coupling: float  # legacy geometry uniform name: stores F, NOT the Newell driver C
    travel: float
    envelope: float
    stage: str
    explanation: str
    upstream: Wind
    source_time: float
    bow_nose: float
    equilibrium_r0: float
    equilibrium_bow: float
    newell: float
    tail_loading: float
    tail_release: float
    front_x: float
    front_width: float
    front_opacity: float

    @property
    def bow_r0(self):
        return self.bow_nose

    @property
    def lag_seconds(self):
        return (self.time-self.source_time)*TIME_SCALE

    @property
    def bow_extrapolated(self):
        """Jelínek 2012 recommends dayside low latitudes, 0.6–11 nPa."""
        return not .6 <= self.pressure <= 11

    @property
    def alfven_mach_bound(self):
        return float(alfven_mach_upper_bound(self.speed, self.density, self.bz))

    @property
    def bow_applicable(self):
        return self.alfven_mach_bound > 1


class Timeline:
    """One causal transport history; all times are compressed by TIME_SCALE.

    Uniform time-varying plug advection avoids characteristic crossing. It
    transports a prescribed history, not a compressible shock solution. Boundary
    equilibration (2 min) and storage/release (10/20 min) are teaching choices.
    """
    def __init__(self, parameters: Parameters = Parameters(), *, step=.1):
        if not math.isfinite(step) or not .01 <= step <= .5:
            raise ValueError("Timeline step must be in [.01, .5]")
        self.parameters = parameters
        self.end = 110 + parameters.duration
        self.times = np.linspace(0, self.end, math.ceil(self.end/step)+1)
        p, t = parameters, self.times
        source = self._source(t)
        dt = np.diff(t)
        self.displacement = np.r_[0, np.cumsum((source[:-1, 0]+source[1:, 0])*.5*dt*TIME_SCALE/EARTH_RADIUS_KM)]
        source_times = self.source_time(t, DRIVER_X)
        wind = self._source(source_times)
        speed, density, bz, envelope = wind.T
        pressure = PROTON_MASS*1e21*density*speed**2
        r0, alpha = shue_parameters(pressure, bz)
        nose = jelinek_nose(pressure)
        driver = newell_driver(speed, bz)
        response = np.column_stack((r0, alpha, nose))
        storage = np.zeros_like(t)
        if p.quiet:
            storage[0] = driver[0]/(driver[0]+.5)
        for i in range(1, len(t)):
            decay = math.exp(-dt[i-1]/2)
            response[i] = response[i-1]*decay + response[i]*(1-decay)
            # Exact constant-driver step of dF/d(min) = C(1-F)/10 - F/20.
            rate = driver[i]/10+.05
            equilibrium = (driver[i]/10)/rate
            storage[i] = equilibrium+(storage[i-1]-equilibrium)*math.exp(-rate*dt[i-1])
        self.values = np.column_stack((speed, density, bz, pressure, response[:, :2], storage, self.displacement, envelope))
        self.bow_values = response[:, 2]
        self.bow_applicable = alfven_mach_upper_bound(speed, density, bz) > 1
        self.drivers = driver
        self.source_values = source
        self.source_pressures = PROTON_MASS*1e21*source[:, 1]*source[:, 0]**2
        self.arrival = float(np.interp(self._distance(EVENT_START)+UPSTREAM_X-DRIVER_X, self.displacement, t))

    def _source(self, time):
        """Prescribed compression/sheath precedes magnetic-cloud orientation."""
        p, t = self.parameters, np.asarray(time)
        envelope = smoothstep((t-EVENT_START)/2)*(1-smoothstep((t-EVENT_START-p.duration)/12))
        magnetic = smoothstep((t-EVENT_START-6)/4)*(1-smoothstep((t-EVENT_START-p.duration)/12))
        sheath = smoothstep((t-EVENT_START)/2)*(1-smoothstep((t-EVENT_START-4)/4))
        if p.quiet:
            envelope = np.ones_like(t); magnetic = envelope; sheath = np.zeros_like(t)
        return np.stack((400+(p.speed-400)*envelope,
                         5+(p.density-5)*envelope+.25*p.density*sheath,
                         3+(p.bz-3)*magnetic, envelope), axis=-1)

    def _distance(self, time):
        t = np.asarray(time)
        # Extend prehistory with the initial speed. Public samples clamp to end.
        d = np.interp(t, self.times, self.displacement)
        return np.where(t < 0, t*self.source_initial_speed*TIME_SCALE/EARTH_RADIUS_KM, d)

    @property
    def source_initial_speed(self):
        return self.parameters.speed if self.parameters.quiet else 400.

    def source_time(self, time, x=DRIVER_X):
        d = self._distance(time)-(UPSTREAM_X-np.asarray(x))
        source = np.interp(d, self.displacement, self.times)
        return np.where(d < 0, d*EARTH_RADIUS_KM/(self.source_initial_speed*TIME_SCALE), source)

    def wind_at(self, time, x=DRIVER_X):
        return self._source(self.source_time(time, x))

    def wind_profile(self, time):
        """RGBA: density/5, Bz(nT), speed(km/s), envelope; sampled by GLSL."""
        values = self.wind_at(time, PROFILE_X)
        return np.ascontiguousarray(np.column_stack((values[:, 1]/5, values[:, 2], values[:, 0], values[:, 3])), dtype=np.float32)

    def auroral_driver(self, time):
        s = self.sample(time)
        return AuroralDriver(s.time*TIME_SCALE, Wind(s.speed, s.density, s.bz, s.envelope),
                             s.newell, s.coupling, s.tail_release, self.parameters.tilt)

    def sample(self, time: float) -> State:
        if not math.isfinite(time):
            raise ValueError("Time must be finite")
        t = float(np.clip(time, 0, self.end))
        v = [float(np.interp(t, self.times, col)) for col in self.values.T]
        wind = Wind(*map(float, self.wind_at(t)))
        v[:4] = [wind.speed, wind.density, wind.bz, wind.pressure]
        v[8] = wind.envelope
        source_time = float(self.source_time(t))
        if self.parameters.quiet or t < EVENT_START:
            stage, explanation = "A · Quiet magnetosphere", "Solar-wind pressure balances magnetic pressure at the boundary. The tail extends away from the Sun."
            if self.parameters.quiet and (self.parameters.speed, self.parameters.density, self.parameters.bz) != (400, 5, 3):
                stage = "A · Steady solar wind"
        elif source_time < EVENT_START:
            stage, explanation = "B · Incoming disturbance", "The +40 R_E monitor has changed; the +24 R_E near-Earth input still sees quiet wind. Compare the two readouts."
        elif source_time < EVENT_START+10:
            stage = "C · Pressure pulse & sheath"
            explanation = ("Higher ρv² compresses the empirical boundaries before the magnetic cloud arrives. Their displayed positions relax toward the equilibrium targets."
                           if wind.pressure > dynamic_pressure(5, 400) else
                           "This chosen wind has lower ρv² than the initial wind; the boundaries expand. A density sheath precedes the magnetic cloud in this prescribed history.")
        elif source_time < EVENT_START+self.parameters.duration:
            stage = "D · Disturbed magnetosphere"
            explanation = ("Southward IMF increases the Newell dayside driver. A schematic flux budget loads the tail; open-line examples explain connectivity, not solved reconnection."
                           if v[2] < 0 else "Northward IMF weakens dayside coupling. Pressure still compresses the boundary; compare the southward preset at the same time.")
        else:
            stage, explanation = "E · Recovery", "Pressure and boundaries recover first. The stored-flux proxy continues to release; this delayed response is not a prediction of a substorm or auroral brightness."
        eq_r0, _ = shue_parameters(wind.pressure, wind.bz)
        c = float(newell_driver(wind.speed, wind.bz))
        front = UPSTREAM_X-float(self._distance(t)-self._distance(EVENT_START))
        width = float(self._distance(EVENT_START+2)-self._distance(EVENT_START))
        return State(t, *v, stage, explanation, Wind(*map(float, self._source(t))), source_time,
                     float(np.interp(t, self.times, self.bow_values)), float(eq_r0), float(jelinek_nose(wind.pressure)),
                     c, c*(1-v[6])/10, v[6]/20, front, width,
                     0. if self.parameters.quiet else float(smoothstep((UPSTREAM_X-front)/2)*smoothstep((front-DRIVER_X+4)/4)))


@lru_cache(maxsize=1)
def topology_seeds():
    """Parametric examples: each has one Earth footpoint and an outer endpoint.

    These are explicitly schematic open connectivity examples, never created by
    cutting closed curves. Their fixed count has no quantitative flux meaning.
    """
    t = np.linspace(0, 1, 120)
    return np.array([np.column_stack((t, np.full_like(t, phi), np.full_like(t, hemisphere)))
                     for hemisphere in (-1, 1) for phi in np.linspace(0, 2*np.pi, 6, endpoint=False)], dtype=np.float32)


def open_line_examples(state, tilt):
    """CPU reference for the optional topology teaching overlay (not field tracing)."""
    seeds = topology_seeds()
    t, phi, hemisphere = (seeds[..., i] for i in range(3))
    a = np.deg2rad(tilt)
    foot = np.stack((.28*np.cos(phi), .28*np.sin(phi), hemisphere*np.sqrt(1-.28**2)), -1)
    foot = foot @ np.array([[np.cos(a), 0, -np.sin(a)], [0, 1, 0], [np.sin(a), 0, np.cos(a)]])
    b = np.stack((np.full_like(t, state.r0*.3), 2*np.sin(phi), hemisphere*(5+state.r0*.25)), -1)
    c = np.stack((np.full_like(t, -22), 6*np.sin(phi), hemisphere*(9+3*state.coupling)), -1)
    end = np.stack((np.full_like(t, -60), 8*np.sin(phi), hemisphere*(10+4*state.coupling)), -1)
    t = t[..., None]
    return (1-t)**3*foot+3*(1-t)**2*t*b+3*(1-t)*t*t*c+t**3*end


def dipole_field(points, tilt=11):
    """Vacuum dipole in nT; magnetic moment points south in GSM."""
    p = np.asarray(points, dtype=float)
    r = np.linalg.norm(p, axis=-1, keepdims=True)
    if np.any(r < 1e-8):
        raise ValueError("Dipole field is singular at the origin")
    a = np.deg2rad(tilt)
    moment = -np.array([np.sin(a), 0, np.cos(a)])
    n = p/r
    return 31200*(3*n*np.sum(n*moment, axis=-1, keepdims=True)-moment)/r**3


@lru_cache(maxsize=8)
def dipole_lines(count=72, samples=160):
    """Exact vacuum dipole integral curves r=L sin²(theta), before deformation."""
    curves = []
    for i in range(count):
        shell = (2.3, 4.2, 7., 11., 16., 22.)[i % 6]
        phi = (i//6)*2*np.pi/math.ceil(count/6)
        foot = np.arcsin(np.sqrt(1/shell))
        theta = np.linspace(foot, np.pi-foot, samples)
        r = shell*np.sin(theta)**2
        curves.append(np.column_stack((r*np.sin(theta)*np.cos(phi),
                                       r*np.sin(theta)*np.sin(phi), r*np.cos(theta))))
    return np.array(curves, dtype=np.float32)


def deform_lines(points, state: State, tilt: float):
    """CPU reference of GPU mapping; educational geometry, NOT a vector field.

    Smoothly stretches the nightside and bounds curves inside the Shue surface.
    Footpoints remain on the unit sphere; no topology change is represented.
    """
    a = np.deg2rad(tilt)
    rotation = np.array([[np.cos(a), 0, np.sin(a)], [0, 1, 0], [-np.sin(a), 0, np.cos(a)]])
    p = np.asarray(points) @ rotation.T
    r = np.linalg.norm(p, axis=-1)
    night = smoothstep(-p[..., 0]/np.maximum(r, 1))
    p[..., 0] -= night*(r-1)*(2.4+3*state.coupling)
    p[..., 0] = np.where(p[..., 0] < -1, -1-54*np.tanh((-p[..., 0]-1)/54), p[..., 0])
    r = np.linalg.norm(p, axis=-1)
    cos_theta = np.clip(p[..., 0]/r, -.9999, 1)
    boundary = state.r0*(2/(1+cos_theta))**state.alpha*.93
    mapped = 1+(r-1)/(1+((r-1)/(boundary-1))**4)**.25
    return p*(mapped/r)[..., None]


def tail_angle(state: State, tail_x=-60):
    lo, hi = np.pi/2, np.pi-.01
    for _ in range(24):
        mid = (lo+hi)/2
        if shue_radius(mid, state.r0, state.alpha)*np.cos(mid) < tail_x:
            hi = mid
        else:
            lo = mid
    return (lo+hi)/2
