"""Binary Light Lab's original C++ photometry, with CAst recording/file adapters."""
from __future__ import annotations

import ctypes as ct
import math
from pathlib import Path
import sys
import weakref

import numpy as np

PARAMETERS = ("mass_a", "radius_a", "temperature_a", "limb_a", "mass_b", "radius_b", "temperature_b", "limb_b",
              "separation", "eccentricity", "inclination", "omega", "node", "distance", "third_light", "exposure", "band")
_DP = ct.POINTER(ct.c_double)
_library = None


def load_library():
    global _library
    if _library is not None:
        return _library
    name = "cast_stellar_lab.dll" if sys.platform == "win32" else ("libcast_stellar_lab.dylib" if sys.platform == "darwin" else "libcast_stellar_lab.so")
    path = Path(__file__).resolve().parents[1] / "native" / name
    try:
        lib = ct.CDLL(str(path))
    except OSError as exc:
        raise RuntimeError(f"Binary Light Lab engine unavailable ({name}). Build native/binary_light for this platform. {exc}") from exc
    signatures = {
        "validate": ([_DP], ct.c_char_p), "preset": ([ct.c_int, _DP], None),
        "preset_text": ([ct.c_int, ct.c_int], ct.c_char_p), "type_text": ([ct.c_int, ct.c_int], ct.c_char_p),
        "type_count": ([], ct.c_int), "period": ([_DP], ct.c_double),
        "set_period": ([_DP, ct.c_double], ct.c_double), "roche": ([_DP, ct.c_int], ct.c_double),
        "observe": ([_DP, ct.c_double, _DP], None), "curve": ([_DP, ct.c_int, _DP], None),
        "geometry": ([_DP, ct.c_double, _DP], None), "shader": ([ct.c_int], ct.c_char_p),
        "blocked": ([ct.c_double]*4 + [ct.c_int], ct.c_double),
        "rng_create": ([], ct.c_void_p), "rng_delete": ([ct.c_void_p], None),
        "rng_reset": ([ct.c_void_p], None), "noise": ([ct.c_void_p, ct.c_double], ct.c_double),
    }
    for name, (args, result) in signatures.items():
        fn = getattr(lib, "bl_" + name)
        fn.argtypes, fn.restype = args, result
    _library = lib
    return lib


def pointer(array):
    return array.ctypes.data_as(_DP)


class BinaryLightModel:
    def __init__(self):
        self.lib = load_library()
        self.values = np.zeros(17, dtype=np.float64)
        self.noise_ppm = 0.0
        self.comparison = 1.0
        self.samples = []
        self.cycles = self.next_sample = 0.0
        self.rng = self.lib.bl_rng_create()
        if not self.rng:
            raise MemoryError("Cannot create observation generator")
        self._cleanup = weakref.finalize(self, self.lib.bl_rng_delete, self.rng)
        self.set_preset(0)

    def get(self, name):
        return float(self.values[PARAMETERS.index(name)])

    def set(self, name, value):
        self.values[PARAMETERS.index(name)] = value

    @property
    def invalid(self):
        if not np.isfinite(self.values).all() or self.get("band") not in range(4):
            return "All parameters must be finite; band must be an integer from 0 to 3."
        return self.lib.bl_validate(pointer(self.values)).decode()

    @property
    def period(self):
        return self.lib.bl_period(pointer(self.values))

    def set_period(self, days):
        self.set("separation", self.lib.bl_set_period(pointer(self.values), days))

    def roche(self, star):
        return self.lib.bl_roche(pointer(self.values), star)

    def set_preset(self, index):
        if index not in range(6):
            raise ValueError("Unknown binary preset")
        self.lib.bl_preset(index, pointer(self.values))
        self.restart()

    def observe(self, phase):
        if self.invalid:
            raise ValueError(self.invalid)
        result = np.empty(5)
        self.lib.bl_observe(pointer(self.values), phase, pointer(result))
        return result

    def curve(self, count=601):
        if self.invalid:
            raise ValueError(self.invalid)
        if count < 2:
            raise ValueError("At least two samples are required")
        result = np.empty((count, 5))
        self.lib.bl_curve(pointer(self.values), count, pointer(result))
        return result

    def geometry(self, phase, face_on=False):
        values = self.values.copy()
        if face_on:
            values[10] = values[12] = 0
        result = np.empty(9)
        self.lib.bl_geometry(pointer(values), phase, pointer(result))
        return result

    def restart(self, phase=0.0):
        self.cycles = self.next_sample = phase
        self.samples.clear()
        self.lib.bl_rng_reset(self.rng)

    def advance(self, dt, speed):
        if self.invalid:
            return
        self.cycles += min(.1, max(0., dt)) * speed
        while self.next_sample <= self.cycles + 1e-10:
            ideal = self.observe(self.next_sample)[0]
            measured = ideal
            if self.noise_ppm > 0:
                measured += self.lib.bl_noise(self.rng, self.noise_ppm * 1e-6)
            self.samples.append((self.next_sample, max(1e-8, measured), ideal))
            self.next_sample += 1 / 600
        del self.samples[:-6000]

    def save(self, path):
        if self.invalid:
            raise ValueError(self.invalid)
        rows = ["BINARY_LIGHT_LAB 1"]
        for values in (self.values[:4], self.values[4:8], self.values[8:13], self.values[13:16]):
            rows.append(" ".join(format(float(x), ".17g") for x in values))
        rows[-1] += f" {int(self.values[16])}"
        Path(path).write_text("\n".join(rows) + "\n", encoding="utf-8")

    def load(self, path):
        words = Path(path).read_text(encoding="utf-8").split()
        if words[:2] != ["BINARY_LIGHT_LAB", "1"] or len(words) < 19:
            raise ValueError("Not a complete supported Binary Light Lab system")
        candidate = np.array([float(v) for v in words[2:19]])
        previous = self.values
        self.values = candidate
        error = self.invalid
        if error:
            self.values = previous
            raise ValueError(error)
        self.restart()

    def export(self, path, observed=False):
        curve = self.curve(1001)
        p = self.get
        headers = ["Binary Light Lab; ideal model; phase 0 = periastron (circular: reference epoch)",
                   f"period_days={self.period:.12g}; a_Rsun={p('separation'):.12g}; eccentricity={p('eccentricity'):.12g}; inclination_deg={p('inclination'):.12g}; omega_deg={p('omega'):.12g}; node_deg={p('node'):.12g}"]
        for j, suffix in enumerate(("a", "b")):
            headers.append(f"star={j+1}; mass_Msun={p('mass_'+suffix):.12g}; radius_Rsun={p('radius_'+suffix):.12g}; temperature_K={p('temperature_'+suffix):.12g}; linear_limb={p('limb_'+suffix):.12g}")
        headers.append(f"band={int(p('band'))}; distance_pc={p('distance'):.12g}; third_light_fraction={p('third_light'):.12g}; exposure_s={p('exposure'):.12g}")
        phase = np.linspace(0, 1, 1001)
        path = Path(path)
        with path.open("w", encoding="utf-8", newline="") as f:
            f.write("".join("# " + line + "\n" for line in headers))
            f.write("phase,time_days,relative_flux,delta_magnitude,irradiance_W_m2,visible_primary_flux,visible_secondary_flux\n")
            np.savetxt(f, np.column_stack((phase, phase*self.period, curve)), delimiter=",", fmt="%.12g")
        if observed:
            output = path.with_name(path.stem + "-observed.csv")
            with output.open("w", encoding="utf-8", newline="") as f:
                f.write(f"# Gaussian noise in normalized flux; seed=2026; sigma_ppm={self.noise_ppm:.12g}; comparison_flux_ratio={self.comparison:.12g}\n")
                f.write("cycles,time_days,phase,ideal_flux,measured_flux,differential_magnitude\n")
                for cycle, measured, ideal in self.samples:
                    f.write(",".join(format(v, ".12g") for v in (cycle, cycle*self.period, cycle % 1, ideal, measured, -2.5*math.log10(measured/self.comparison))) + "\n")
            return output
        return path
