from __future__ import annotations

from dataclasses import dataclass
from functools import lru_cache
from typing import Any, Callable

import numpy as np

try:
    import cupy as _cupy
except Exception:
    _cupy = None


@dataclass(frozen=True, slots=True)
class SyntheticTrackingArrayBackend:
    name: str
    xp: Any
    is_gpu: bool
    _to_numpy: Callable[[Any], np.ndarray]

    def asarray(self, value: Any, *, dtype: Any | None = None) -> Any:
        return self.xp.asarray(value, dtype=dtype)

    def to_numpy(self, value: Any, *, dtype: Any | None = None) -> np.ndarray:
        result = np.asarray(self._to_numpy(value))
        if dtype is None:
            return result
        return np.asarray(result, dtype=dtype)


def create_array_backend(
    *,
    name: str,
    xp: Any,
    is_gpu: bool,
    to_numpy: Callable[[Any], np.ndarray] | None = None,
) -> SyntheticTrackingArrayBackend:
    converter = np.asarray if to_numpy is None else to_numpy
    return SyntheticTrackingArrayBackend(name=name, xp=xp, is_gpu=is_gpu, _to_numpy=converter)


NUMPY_ARRAY_BACKEND = create_array_backend(name="cpu", xp=np, is_gpu=False)
_CUPY_ARRAY_BACKEND = (
    None
    if _cupy is None
    else create_array_backend(name="gpu", xp=_cupy, is_gpu=True, to_numpy=_cupy.asnumpy)
)


def cupy_available() -> bool:
    return _CUPY_ARRAY_BACKEND is not None


@lru_cache(maxsize=1)
def _cupy_runtime_status() -> tuple[bool, str | None]:
    if _CUPY_ARRAY_BACKEND is None or _cupy is None:
        return False, "CuPy is not installed."
    try:
        if int(_cupy.cuda.runtime.getDeviceCount()) <= 0:
            return False, "No CUDA device is available."
        _cupy.asnumpy(_cupy.arange(1, dtype=_cupy.float32))
    except Exception as exc:
        error_text = " ".join(str(exc).split())
        if len(error_text) > 220:
            error_text = f"{error_text[:217]}..."
        return False, f"CuPy could not initialize a compatible CUDA runtime ({error_text})."
    return True, None


def resolve_full_frame_backend(
    *,
    preference: str,
    integration_mode: str,
    rejection_mode: str,
) -> tuple[SyntheticTrackingArrayBackend, str | None]:
    normalized_preference = str(preference or "auto").strip().lower()
    if normalized_preference not in {"auto", "cpu", "gpu"}:
        normalized_preference = "auto"

    normalized_integration = str(integration_mode or "average").strip().lower()
    normalized_rejection = str(rejection_mode or "no_rejection").strip().lower()

    if normalized_rejection != "no_rejection":
        return (
            NUMPY_ARRAY_BACKEND,
            "Using CPU fallback: GPU full-frame acceleration currently supports only No rejection stacks.",
        )

    if normalized_integration not in {"average", "mean", "min", "max"}:
        return (
            NUMPY_ARRAY_BACKEND,
            "Using CPU fallback: GPU full-frame acceleration currently supports only Average, Mean, Min, and Max.",
        )

    if normalized_preference == "cpu":
        return NUMPY_ARRAY_BACKEND, None

    if _CUPY_ARRAY_BACKEND is not None:
        runtime_usable, runtime_note = _cupy_runtime_status()
        if runtime_usable:
            return _CUPY_ARRAY_BACKEND, None
        return NUMPY_ARRAY_BACKEND, f"Using CPU fallback: {runtime_note}"

    if normalized_preference == "gpu":
        return NUMPY_ARRAY_BACKEND, "GPU full-frame acceleration was requested, but CuPy is not installed. Using CPU."

    return NUMPY_ARRAY_BACKEND, None