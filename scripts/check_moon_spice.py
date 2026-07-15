from __future__ import annotations

import argparse
import importlib
import json
import math
from pathlib import Path
from typing import Sequence

from photometry_app.ui.moon_system import (
    DEFAULT_LUNAR_ORIENTATION_COMPARISON_CASES,
    LunarOrientationApprox,
    LunarOrientationSpiceProvider,
    Matrix3,
    Vector3,
    compare_lunar_orientation_providers,
    select_lunar_orientation_provider,
)


def _finite_vector(vector: Vector3) -> bool:
    return all(math.isfinite(float(component)) for component in vector)


def _finite_matrix(matrix: Matrix3) -> bool:
    return all(_finite_vector(row) for row in matrix)


def _matrix_is_orthonormal(matrix: Matrix3, *, tolerance: float = 1.0e-6) -> bool:
    rows = matrix
    for row in rows:
        if abs(sum(component * component for component in row) - 1.0) > tolerance:
            return False
    for lhs_index in range(3):
        for rhs_index in range(lhs_index + 1, 3):
            dot_value = sum(rows[lhs_index][axis] * rows[rhs_index][axis] for axis in range(3))
            if abs(dot_value) > tolerance:
                return False
    return True


def _normalize(vector: Vector3) -> Vector3:
    length = math.sqrt(vector[0] * vector[0] + vector[1] * vector[1] + vector[2] * vector[2])
    if length <= 1.0e-12:
        return (0.0, 0.0, 1.0)
    return (vector[0] / length, vector[1] / length, vector[2] / length)


def _equatorial_unit_vector(ra_deg: float, dec_deg: float) -> Vector3:
    ra_rad = math.radians(float(ra_deg) % 360.0)
    dec_rad = math.radians(max(-90.0, min(90.0, float(dec_deg))))
    cos_dec = math.cos(dec_rad)
    return (cos_dec * math.cos(ra_rad), cos_dec * math.sin(ra_rad), math.sin(dec_rad))


def _spiceypy_import_status(spice_module: object | None) -> tuple[bool, str]:
    if spice_module is not None:
        return True, "provided by caller"
    try:
        importlib.import_module("spiceypy")
    except Exception as exc:
        return False, f"{type(exc).__name__}: {exc}"
    return True, "importable"


def _comparison_summary(comparisons: Sequence[object]) -> dict[str, object]:
    if not comparisons:
        return {"case_count": 0}
    return {
        "case_count": len(comparisons),
        "max_body_axis_delta_deg": max(float(item.max_body_axis_delta_deg) for item in comparisons),
        "max_libration_longitude_delta_deg": max(float(item.libration_longitude_delta_deg) for item in comparisons),
        "max_libration_latitude_delta_deg": max(float(item.libration_latitude_delta_deg) for item in comparisons),
        "max_lunar_north_position_angle_delta_deg": max(float(item.lunar_north_position_angle_delta_deg) for item in comparisons),
    }


def build_moon_spice_diagnostics_report(
    project_root: Path,
    *,
    mode: str | None = None,
    kernel_paths: Sequence[Path] | None = None,
    spice_module: object | None = None,
) -> dict[str, object]:
    spiceypy_importable, spiceypy_import_error = _spiceypy_import_status(spice_module)
    selection = select_lunar_orientation_provider(
        mode,
        kernel_paths=kernel_paths,
        project_root=project_root,
        spice_module=spice_module,
    )
    diagnostics = selection.diagnostics
    orientation_checks: list[dict[str, object]] = []
    for case in DEFAULT_LUNAR_ORIENTATION_COMPARISON_CASES:
        try:
            state = selection.provider.compute(
                case.observation_time_utc,
                ra_deg=case.ra_deg,
                dec_deg=case.dec_deg,
                observer_latitude_deg=case.observer_latitude_deg,
                observer_longitude_deg=case.observer_longitude_deg,
                moon_direction_equatorial=_equatorial_unit_vector(case.ra_deg, case.dec_deg),
                moon_to_sun_direction_equatorial=_normalize(case.moon_to_sun_direction_equatorial),
            )
            finite = (
                math.isfinite(float(state.libration_longitude_rad))
                and math.isfinite(float(state.libration_latitude_rad))
                and math.isfinite(float(state.lunar_north_position_angle_rad))
                and math.isfinite(float(state.parallactic_angle_rad))
                and _finite_matrix(state.moon_to_equatorial_matrix)
                and _finite_matrix(state.equatorial_to_moon_matrix)
                and _finite_matrix(state.view_to_moon_matrix)
                and _finite_vector(state.sun_dir_moon_local)
                and _finite_vector(state.observer_dir_moon_local)
                and _finite_vector(state.earth_dir_moon_local)
            )
            orthonormal = _matrix_is_orthonormal(state.equatorial_to_moon_matrix) and _matrix_is_orthonormal(state.view_to_moon_matrix)
            orientation_checks.append(
                {
                    "label": case.label,
                    "finite": finite,
                    "orthonormal": orthonormal,
                    "error": "",
                }
            )
        except Exception as exc:
            orientation_checks.append(
                {
                    "label": case.label,
                    "finite": False,
                    "orthonormal": False,
                    "error": f"{type(exc).__name__}: {exc}",
                }
            )

    comparisons = []
    comparison_summary = {"case_count": 0}
    if diagnostics.active_provider == "spice":
        try:
            comparison_results = compare_lunar_orientation_providers(selection.provider)
            comparisons = [
                {
                    "label": result.case.label,
                    "max_body_axis_delta_deg": result.max_body_axis_delta_deg,
                    "libration_longitude_delta_deg": result.libration_longitude_delta_deg,
                    "libration_latitude_delta_deg": result.libration_latitude_delta_deg,
                    "lunar_north_position_angle_delta_deg": result.lunar_north_position_angle_delta_deg,
                }
                for result in comparison_results
            ]
            comparison_summary = _comparison_summary(comparison_results)
        except Exception as exc:
            comparison_summary = {"case_count": 0, "error": f"{type(exc).__name__}: {exc}"}

    all_finite = all(bool(item["finite"]) for item in orientation_checks)
    all_orthonormal = all(bool(item["orthonormal"]) for item in orientation_checks)
    spice_provider = selection.provider if isinstance(selection.provider, LunarOrientationSpiceProvider) else None
    return {
        "spiceypy_importable": spiceypy_importable,
        "spiceypy_import_error": "" if spiceypy_importable else spiceypy_import_error,
        "selected_provider_mode": diagnostics.requested_mode,
        "orientation_provider_mode": diagnostics.requested_mode,
        "discovered_kernel_paths": list(diagnostics.spice_kernel_paths),
        "kernel_count": len(diagnostics.spice_kernel_paths),
        "loaded_kernel_paths": list(diagnostics.loaded_kernel_paths),
        "loaded_kernel_count": diagnostics.loaded_kernel_count,
        "active_orientation_provider": diagnostics.active_provider,
        "orientation_provider_active": diagnostics.active_provider,
        "spice_active": diagnostics.active_provider == "spice",
        "spice_available": diagnostics.spice_available,
        "fallback_reason": diagnostics.fallback_reason,
        "provider_diagnostic": diagnostics.diagnostic,
        "kernel_discovery_diagnostic": diagnostics.kernel_discovery_diagnostic,
        "spice_active_frame": spice_provider.active_frame if spice_provider is not None else "",
        "spice_compute_fallback_count": spice_provider.compute_fallback_count if spice_provider is not None else 0,
        "spice_compute_fallback_reason": spice_provider.last_compute_error if spice_provider is not None else "",
        "orientation_checks": orientation_checks,
        "all_orientation_checks_finite": all_finite,
        "all_orientation_checks_orthonormal": all_orthonormal,
        "comparison": comparisons,
        "comparison_summary": comparison_summary,
        "safe_fallback": diagnostics.active_provider == "approx" and all_finite and all_orthonormal,
        "status": "ok" if all_finite and all_orthonormal else "failed",
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Check Moon SPICE orientation provider setup and safe fallback behavior.")
    parser.add_argument("--project-root", default=str(Path(__file__).resolve().parents[1]))
    parser.add_argument("--mode", choices=("auto", "approx", "spice"), default=None)
    parser.add_argument("--kernel", action="append", default=None, help="Explicit kernel path. Repeat to provide an ordered kernel list.")
    parser.add_argument("--output", default="", help="Optional JSON output path. Prints JSON to stdout when omitted.")
    args = parser.parse_args()

    project_root = Path(args.project_root).resolve()
    kernel_paths = tuple(Path(value).resolve() for value in args.kernel) if args.kernel else None
    report = build_moon_spice_diagnostics_report(project_root, mode=args.mode, kernel_paths=kernel_paths)
    encoded = json.dumps(report, indent=2)
    if args.output:
        output_path = Path(args.output)
        if not output_path.is_absolute():
            output_path = project_root / output_path
        output_path.write_text(encoded, encoding="utf-8")
        print(output_path)
    else:
        print(encoded)
    return 0 if report.get("status") == "ok" else 1


if __name__ == "__main__":
    raise SystemExit(main())
