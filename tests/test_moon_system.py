from __future__ import annotations

from concurrent.futures import Future
from dataclasses import replace
from datetime import UTC, datetime, timedelta
import inspect
import json
import math
import os
from pathlib import Path
import tempfile
from typing import cast
import unittest
from unittest.mock import patch

from PySide6.QtGui import QColor, QImage

from photometry_app.ui.moon_system import (
    DEFAULT_MOON_VISUAL_SMOKE_CASES,
    DEFAULT_MOON_VISUAL_SETTINGS,
    IDENTITY_MATRIX_3,
    LunarOrientationApprox,
    LunarOrientationSpiceProvider,
    LunarOrientationState,
    MOON_SPICE_KERNELS_ENV_VAR,
    MOON_SPICE_KERNEL_DIR_ENV_VAR,
    MoonAtmosphereModel,
    MoonCache,
    MoonDrawState,
    MoonEarthshineModel,
    MoonAsyncEphemerisSampler,
    MoonEphemeris,
    MoonEphemerisValidationCase,
    MoonRendererGL,
    MoonPolarCapBinding,
    MoonState,
    MoonTileDiagnostics,
    MoonTileKey,
    MoonTileSettings,
    MoonTileShaderBinding,
    MoonTileUvBounds,
    MoonTextureTile,
    MoonTiledTextureManager,
    MoonTextureLod,
    _MoonTextureCacheEntry,
    compare_lunar_orientation_providers,
    select_lunar_orientation_provider,
    validate_moon_ephemeris_against_astropy,
)
from scripts.check_moon_spice import build_moon_spice_diagnostics_report


def _assert_finite_vector(test_case: unittest.TestCase, vector: tuple[float, float, float]) -> None:

    for component in vector:
        test_case.assertTrue(math.isfinite(component))


def _assert_finite_matrix(test_case: unittest.TestCase, matrix: tuple[tuple[float, float, float], tuple[float, float, float], tuple[float, float, float]]) -> None:

    for row in matrix:
        _assert_finite_vector(test_case, row)


def _assert_finite_moon_state(test_case: unittest.TestCase, state: MoonState) -> None:

    for value in (
        state.ra_deg,
        state.dec_deg,
        state.altitude_deg,
        state.azimuth_deg,
        state.distance_km,
        state.angular_diameter_deg,
        state.phase_angle_deg,
        state.illuminated_fraction,
        state.libration_longitude_rad,
        state.libration_latitude_rad,
        state.lunar_north_position_angle_rad,
        state.parallactic_angle_rad,
    ):
        test_case.assertTrue(math.isfinite(value))
    _assert_finite_vector(test_case, state.moon_direction_equatorial)
    _assert_finite_vector(test_case, state.moon_to_sun_direction_equatorial)
    _assert_finite_vector(test_case, state.sun_dir_moon_local)
    _assert_finite_vector(test_case, state.earth_dir_moon_local)
    _assert_finite_vector(test_case, state.observer_dir_moon_local)
    _assert_finite_matrix(test_case, state.view_to_moon_matrix)
    _assert_finite_matrix(test_case, state.moon_to_equatorial_matrix)

def _transpose(matrix: tuple[tuple[float, float, float], tuple[float, float, float], tuple[float, float, float]]) -> tuple[tuple[float, float, float], tuple[float, float, float], tuple[float, float, float]]:

    return (
        (matrix[0][0], matrix[1][0], matrix[2][0]),
        (matrix[0][1], matrix[1][1], matrix[2][1]),
        (matrix[0][2], matrix[1][2], matrix[2][2]),
    )


def _rotation_z(angle_rad: float) -> tuple[tuple[float, float, float], tuple[float, float, float], tuple[float, float, float]]:

    cos_angle = math.cos(angle_rad)
    sin_angle = math.sin(angle_rad)
    return ((cos_angle, -sin_angle, 0.0), (sin_angle, cos_angle, 0.0), (0.0, 0.0, 1.0))


def _angle_distance_rad(lhs_rad: float, rhs_rad: float) -> float:

    return abs(((lhs_rad - rhs_rad + math.pi) % (2.0 * math.pi)) - math.pi)


def _orientation_state(angle_rad: float) -> LunarOrientationState:

    matrix = _rotation_z(angle_rad)
    return LunarOrientationState(
        libration_longitude_rad=angle_rad,
        libration_latitude_rad=0.1 * math.sin(angle_rad),
        lunar_north_position_angle_rad=angle_rad * 0.5,
        parallactic_angle_rad=-angle_rad * 0.25,
        moon_to_equatorial_matrix=_transpose(matrix),
        equatorial_to_moon_matrix=matrix,
        view_to_moon_matrix=matrix,
        moon_to_view_matrix=_transpose(matrix),
        sun_dir_moon_local=(0.0, 0.0, 1.0),
        observer_dir_moon_local=(0.0, 0.0, 1.0),
        earth_dir_moon_local=(0.0, 0.0, 1.0),
    )


class _StaticOrientationProvider:

    def __init__(self, state: LunarOrientationState) -> None:

        self._state = state

    def compute(self, *_args, **_kwargs) -> LunarOrientationState:  # type: ignore[no-untyped-def]

        return self._state


class _FakeSpiceModule:

    def __init__(self, matrix: tuple[tuple[float, float, float], tuple[float, float, float], tuple[float, float, float]]) -> None:

        self.matrix = matrix
        self.loaded_kernels: list[str] = []
        self.pxform_calls: list[tuple[str, str, float]] = []

    def furnsh(self, path: str) -> None:

        self.loaded_kernels.append(path)

    def str2et(self, _timestamp: str) -> float:

        return 123456.0

    def pxform(self, from_frame: str, to_frame: str, et_seconds: float):  # type: ignore[no-untyped-def]

        self.pxform_calls.append((from_frame, to_frame, et_seconds))
        return self.matrix


class _FailingPxformSpiceModule(_FakeSpiceModule):

    def pxform(self, from_frame: str, to_frame: str, et_seconds: float):  # type: ignore[no-untyped-def]

        self.pxform_calls.append((from_frame, to_frame, et_seconds))
        raise RuntimeError("missing lunar frame coverage")


def _assert_matrix_orthonormal(test_case: unittest.TestCase, matrix: tuple[tuple[float, float, float], tuple[float, float, float], tuple[float, float, float]]) -> None:

    rows = matrix
    for row in rows:
        test_case.assertAlmostEqual(sum(component * component for component in row), 1.0, delta=1.0e-6)
    for lhs_index in range(3):
        for rhs_index in range(lhs_index + 1, 3):
            test_case.assertAlmostEqual(sum(rows[lhs_index][axis] * rows[rhs_index][axis] for axis in range(3)), 0.0, delta=1.0e-6)


class _ImmediateExecutor:

    def submit(self, function, *args, **kwargs):  # type: ignore[no-untyped-def]

        future: Future = Future()
        try:
            future.set_result(function(*args, **kwargs))
        except Exception as exc:
            future.set_exception(exc)
        return future


class _DeferredExecutor:

    def __init__(self) -> None:

        self.future: Future | None = None

    def submit(self, _function, *_args, **_kwargs):  # type: ignore[no-untyped-def]

        self.future = Future()
        return self.future


class _TileDeferredExecutor:

    def __init__(self) -> None:

        self.tile_futures: list[Future] = []

    def submit(self, function, *args, **kwargs):  # type: ignore[no-untyped-def]

        if getattr(function, "__name__", "") == "_load_tile":
            future: Future = Future()
            self.tile_futures.append(future)
            return future
        future = Future()
        try:
            future.set_result(function(*args, **kwargs))
        except Exception as exc:
            future.set_exception(exc)
        return future


class _DiagnosticsDeferredExecutor:

    DEFERRED_FUNCTIONS = {
        "_compute_live_equivalence_report",
        "_compute_normal_tile_diagnostics",
        "_compute_polar_cap_binding",
    }

    def __init__(self) -> None:

        self.deferred_names: list[str] = []
        self.deferred_futures: list[Future] = []

    def submit(self, function, *args, **kwargs):  # type: ignore[no-untyped-def]

        function_name = getattr(function, "__name__", "")
        if function_name in self.DEFERRED_FUNCTIONS:
            future: Future = Future()
            self.deferred_names.append(function_name)
            self.deferred_futures.append(future)
            return future
        future = Future()
        try:
            future.set_result(function(*args, **kwargs))
        except Exception as exc:
            future.set_exception(exc)
        return future


class _FakeUniformFunctions:

    def __init__(self, uniform_locations: dict[str, int]) -> None:

        self.location_names = {location: name for name, location in uniform_locations.items()}
        self.values: dict[str, object] = {}

    def glUniform1i(self, location: int, value: int) -> None:

        self.values[self.location_names[location]] = int(value)

    def glUniform1f(self, location: int, value: float) -> None:

        self.values[self.location_names[location]] = float(value)

    def glUniform2f(self, location: int, lhs: float, rhs: float) -> None:

        self.values[self.location_names[location]] = (float(lhs), float(rhs))

    def glUniform3f(self, location: int, lhs: float, middle: float, rhs: float) -> None:

        self.values[self.location_names[location]] = (float(lhs), float(middle), float(rhs))

    def glUniform4f(self, location: int, first: float, second: float, third: float, fourth: float) -> None:

        self.values[self.location_names[location]] = (float(first), float(second), float(third), float(fourth))


class _FakeTexture:

    def __init__(self) -> None:

        self.released = False
        self.destroyed = False

    def release(self) -> None:

        self.released = True

    def destroy(self) -> None:

        self.destroyed = True


def _fake_state(observation_time: datetime, altitude_deg: float, *, azimuth_deg: float = 90.0) -> MoonState:

    return MoonState(
        observation_time_utc=observation_time,
        observer_latitude_deg=31.0,
        observer_longitude_deg=-99.0,
        observer_elevation_m=400.0,
        ra_deg=120.0 + altitude_deg,
        dec_deg=-10.0 + altitude_deg * 0.1,
        altitude_deg=altitude_deg,
        azimuth_deg=azimuth_deg,
        distance_km=384_000.0,
        angular_diameter_deg=0.518,
        phase_angle_deg=70.0,
        illuminated_fraction=0.67,
        moon_direction_equatorial=(1.0, 0.0, 0.0),
        moon_to_sun_direction_equatorial=(0.0, 0.0, 1.0),
    )


def _fake_draw_state(diameter_px: float) -> MoonDrawState:

    return MoonDrawState(
        viewport_width_px=1200,
        viewport_height_px=1200,
        center_x_px=600.0,
        center_y_px=600.0,
        radius_px=float(diameter_px) * 0.5,
        opacity=1.0,
        sun_direction_moon_local=(1.0, 0.0, 0.0),
        view_to_moon_matrix=IDENTITY_MATRIX_3,
        selected_lod_label="test",
    )


def _write_moon_tile(root: Path, kind: str, lod_level: int, tile_x: int, tile_y: int, color: QColor | None = None) -> Path:

    tile_dir = root / "assets" / "moon_tiles" / kind / f"L{lod_level}"
    tile_dir.mkdir(parents=True, exist_ok=True)
    path = tile_dir / f"{tile_x}_{tile_y}.png"
    image = QImage(8, 8, QImage.Format.Format_RGB888)
    image.fill(color or QColor(120 + tile_x, 80 + tile_y, 160))
    self_saved = image.save(str(path))
    if not self_saved:
        raise AssertionError(f"Unable to save test tile {path}")
    return path


def _write_full_moon_tile_level(
    root: Path,
    lod_level: int,
    *,
    include_normal: bool = False,
    albedo_color: QColor | None = None,
) -> None:

    tile_columns, tile_rows = MoonTiledTextureManager.tile_grid_dimensions(lod_level)
    for tile_y in range(tile_rows):
        for tile_x in range(tile_columns):
            _write_moon_tile(root, "albedo", lod_level, tile_x, tile_y, albedo_color)
            if include_normal:
                _write_moon_tile(root, "normal", lod_level, tile_x, tile_y, QColor(128, 128, 255))


def _write_global_moon_fixture(path: Path) -> None:

    path.parent.mkdir(parents=True, exist_ok=True)
    image = QImage(32, 16, QImage.Format.Format_RGB888)
    image.fill(QColor(120, 110, 100))
    if not image.save(str(path)):
        raise AssertionError(f"Unable to save test Moon global source {path}")


def _write_registered_tile_manifest(root: Path, global_source_path: Path, *, registered: bool) -> None:

    source_id = MoonTiledTextureManager._sha256_file(global_source_path)
    manifest_path = root / "assets" / "moon_tiles" / "build_manifest.json"
    manifest_path.parent.mkdir(parents=True, exist_ok=True)
    manifest_path.write_text(
        json.dumps(
            {
                "albedo": {"source_sha256": source_id},
                "registration": {
                    "registered_to_active_global_texture": bool(registered),
                    "global_source_sha256": source_id,
                    "detected_longitude_shift_degrees": 0.0,
                    "possible_u_flip_detected": False,
                    "possible_v_flip_detected": False,
                },
            }
        ),
        encoding="utf-8",
    )


def _texture_tile(kind: str, lod_level: int, tile_x: int, tile_y: int) -> MoonTextureTile:

    image = QImage(8, 8, QImage.Format.Format_RGB888)
    image.fill(QColor(120, 130, 140) if kind == "albedo" else QColor(128, 128, 255))
    key = MoonTileKey(kind, lod_level, tile_x, tile_y)
    return MoonTextureTile(
        key=key,
        label=MoonTiledTextureManager._format_tile_key(key),
        path=Path(f"/tmp/{kind}_{lod_level}_{tile_x}_{tile_y}.png"),
        uv_bounds=MoonTiledTextureManager.tile_uv_bounds(key),
        image=image,
        cache_key=("tile", kind, lod_level, tile_x, tile_y),
    )


def _moon_renderer_uniform_names() -> tuple[str, ...]:

    return (
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


class MoonSystemTest(unittest.TestCase):

    def test_lunar_orientation_approx_returns_orthonormal_body_frame(self) -> None:

        provider = LunarOrientationApprox()
        ra_rad = math.radians(140.0)
        dec_rad = math.radians(-12.0)
        moon_direction = (math.cos(dec_rad) * math.cos(ra_rad), math.cos(dec_rad) * math.sin(ra_rad), math.sin(dec_rad))
        state = provider.compute(
            datetime(2026, 5, 25, 12, 0, tzinfo=UTC),
            ra_deg=140.0,
            dec_deg=-12.0,
            observer_latitude_deg=31.5,
            observer_longitude_deg=-99.4,
            moon_direction_equatorial=moon_direction,
            moon_to_sun_direction_equatorial=(-0.4, 0.2, 0.8),
        )

        _assert_matrix_orthonormal(self, state.equatorial_to_moon_matrix)
        _assert_matrix_orthonormal(self, state.view_to_moon_matrix)
        self.assertTrue(math.isfinite(state.libration_longitude_rad))
        self.assertTrue(math.isfinite(state.libration_latitude_rad))
        self.assertAlmostEqual(sum(component * component for component in state.sun_dir_moon_local), 1.0, delta=1.0e-6)
        self.assertAlmostEqual(sum(component * component for component in state.observer_dir_moon_local), 1.0, delta=1.0e-6)

    def test_lunar_orientation_selection_auto_falls_back_without_kernels(self) -> None:

        selection = select_lunar_orientation_provider("auto", kernel_paths=())

        self.assertIsInstance(selection.provider, LunarOrientationApprox)
        self.assertEqual(selection.diagnostics.requested_mode, "auto")
        self.assertEqual(selection.diagnostics.active_provider, "approx")
        self.assertFalse(selection.diagnostics.spice_available)
        self.assertIn("No SPICE", selection.diagnostics.fallback_reason)

    def test_lunar_orientation_selection_approx_forces_approx_even_with_kernels(self) -> None:

        with tempfile.TemporaryDirectory() as temp_dir:

            kernel_path = Path(temp_dir) / "moon.tpc"
            kernel_path.write_text("fake kernel", encoding="utf-8")
            with patch("photometry_app.ui.moon_system.importlib.import_module", side_effect=AssertionError("SPICE import should not be attempted")) as import_module:
                selection = select_lunar_orientation_provider(
                    "approx",
                    kernel_paths=(kernel_path,),
                )

        self.assertIsInstance(selection.provider, LunarOrientationApprox)
        self.assertEqual(selection.diagnostics.active_provider, "approx")
        self.assertEqual(selection.diagnostics.requested_mode, "approx")
        import_module.assert_not_called()

    def test_lunar_orientation_selection_spice_missing_kernel_fails_gracefully(self) -> None:

        with tempfile.TemporaryDirectory() as temp_dir:

            missing_path = Path(temp_dir) / "missing.tpc"
            selection = select_lunar_orientation_provider(
                "spice",
                kernel_paths=(missing_path,),
                spice_module=_FakeSpiceModule(IDENTITY_MATRIX_3),
            )

        self.assertIsInstance(selection.provider, LunarOrientationApprox)
        self.assertEqual(selection.diagnostics.active_provider, "approx")
        self.assertEqual(selection.diagnostics.requested_mode, "spice")
        self.assertIn("Missing SPICE", selection.diagnostics.fallback_reason)
        self.assertIn("using approximate fallback", selection.diagnostics.diagnostic)

    def test_lunar_orientation_selection_spice_missing_module_fails_gracefully(self) -> None:

        with tempfile.TemporaryDirectory() as temp_dir:

            kernel_path = Path(temp_dir) / "moon.tpc"
            kernel_path.write_text("fake kernel", encoding="utf-8")
            with patch("photometry_app.ui.moon_system.importlib.import_module", side_effect=ModuleNotFoundError("no spiceypy")):
                selection = select_lunar_orientation_provider("spice", kernel_paths=(kernel_path,))

        self.assertIsInstance(selection.provider, LunarOrientationApprox)
        self.assertEqual(selection.diagnostics.active_provider, "approx")
        self.assertFalse(selection.diagnostics.spice_available)
        self.assertIn("spiceypy is unavailable", selection.diagnostics.fallback_reason)

    def test_lunar_orientation_selection_invalid_kernel_path_falls_back(self) -> None:

        with tempfile.TemporaryDirectory() as temp_dir:

            invalid_path = Path(temp_dir)
            selection = select_lunar_orientation_provider(
                "auto",
                kernel_paths=(invalid_path,),
                spice_module=_FakeSpiceModule(IDENTITY_MATRIX_3),
            )

        self.assertIsInstance(selection.provider, LunarOrientationApprox)
        self.assertEqual(selection.diagnostics.active_provider, "approx")
        self.assertIn("Missing SPICE", selection.diagnostics.fallback_reason)

    def test_lunar_orientation_selection_invalid_kernel_directory_env_reports_clear_fallback(self) -> None:

        with tempfile.TemporaryDirectory() as temp_dir:

            missing_dir = Path(temp_dir) / "missing-spice"
            with patch.dict(os.environ, {MOON_SPICE_KERNELS_ENV_VAR: "", MOON_SPICE_KERNEL_DIR_ENV_VAR: str(missing_dir)}):
                selection = select_lunar_orientation_provider("auto", project_root=Path(temp_dir), spice_module=_FakeSpiceModule(IDENTITY_MATRIX_3))

        self.assertIsInstance(selection.provider, LunarOrientationApprox)
        self.assertEqual(selection.diagnostics.active_provider, "approx")
        self.assertIn("kernel directory does not exist", selection.diagnostics.fallback_reason)
        self.assertIn(str(missing_dir), selection.diagnostics.kernel_discovery_diagnostic)

    def test_lunar_orientation_selection_auto_uses_spice_when_available(self) -> None:

        with tempfile.TemporaryDirectory() as temp_dir:

            kernel_path = Path(temp_dir) / "moon.tpc"
            kernel_path.write_text("fake kernel", encoding="utf-8")
            fake_spice = _FakeSpiceModule(_rotation_z(0.42))
            selection = select_lunar_orientation_provider(
                "auto",
                kernel_paths=(kernel_path,),
                spice_module=fake_spice,
            )

            self.assertIsInstance(selection.provider, LunarOrientationSpiceProvider)
            self.assertEqual(selection.diagnostics.active_provider, "spice")
            self.assertTrue(selection.diagnostics.spice_available)
            self.assertEqual(selection.diagnostics.loaded_kernel_count, 1)
            self.assertEqual(selection.diagnostics.loaded_kernel_paths, (str(kernel_path),))
            self.assertEqual(fake_spice.loaded_kernels, [str(kernel_path)])

    def test_moon_spice_diagnostics_helper_reports_finite_mock_spice(self) -> None:

        with tempfile.TemporaryDirectory() as temp_dir:

            project_root = Path(temp_dir)
            kernel_path = project_root / "assets" / "spice" / "moon.tpc"
            kernel_path.parent.mkdir(parents=True)
            kernel_path.write_text("fake kernel", encoding="utf-8")
            report = build_moon_spice_diagnostics_report(
                project_root,
                mode="spice",
                kernel_paths=(kernel_path,),
                spice_module=_FakeSpiceModule(_rotation_z(0.2)),
            )

        self.assertTrue(report["spiceypy_importable"])
        self.assertTrue(report["spice_active"])
        self.assertEqual(report["active_orientation_provider"], "spice")
        self.assertEqual(report["loaded_kernel_paths"], [str(kernel_path)])
        self.assertTrue(report["all_orientation_checks_finite"])
        self.assertTrue(report["all_orientation_checks_orthonormal"])
        self.assertEqual(report["comparison_summary"]["case_count"], 3)

    def test_lunar_orientation_spice_provider_returns_finite_orthonormal_state(self) -> None:

        with tempfile.TemporaryDirectory() as temp_dir:

            kernel_path = Path(temp_dir) / "moon.tpc"
            kernel_path.write_text("fake kernel", encoding="utf-8")
            provider = LunarOrientationSpiceProvider((kernel_path,), spice_module=_FakeSpiceModule(_rotation_z(0.33)))
            ra_rad = math.radians(140.0)
            dec_rad = math.radians(-12.0)
            moon_direction = (math.cos(dec_rad) * math.cos(ra_rad), math.cos(dec_rad) * math.sin(ra_rad), math.sin(dec_rad))

            state = provider.compute(
                datetime(2026, 5, 25, 12, 0, tzinfo=UTC),
                ra_deg=140.0,
                dec_deg=-12.0,
                observer_latitude_deg=31.5,
                observer_longitude_deg=-99.4,
                moon_direction_equatorial=moon_direction,
                moon_to_sun_direction_equatorial=(-0.4, 0.2, 0.8),
            )

        _assert_matrix_orthonormal(self, state.equatorial_to_moon_matrix)
        _assert_matrix_orthonormal(self, state.view_to_moon_matrix)
        _assert_finite_vector(self, state.sun_dir_moon_local)
        _assert_finite_vector(self, state.earth_dir_moon_local)
        self.assertTrue(math.isfinite(state.libration_longitude_rad))
        self.assertTrue(math.isfinite(state.lunar_north_position_angle_rad))

    def test_lunar_orientation_spice_provider_compute_failure_falls_back_to_approx(self) -> None:

        with tempfile.TemporaryDirectory() as temp_dir:

            kernel_path = Path(temp_dir) / "moon.tpc"
            kernel_path.write_text("fake kernel", encoding="utf-8")
            provider = LunarOrientationSpiceProvider(
                (kernel_path,),
                spice_module=_FailingPxformSpiceModule(IDENTITY_MATRIX_3),
                fallback_provider=LunarOrientationApprox(),
            )
            ra_rad = math.radians(140.0)
            dec_rad = math.radians(-12.0)
            moon_direction = (math.cos(dec_rad) * math.cos(ra_rad), math.cos(dec_rad) * math.sin(ra_rad), math.sin(dec_rad))

            state = provider.compute(
                datetime(2026, 5, 25, 12, 0, tzinfo=UTC),
                ra_deg=140.0,
                dec_deg=-12.0,
                observer_latitude_deg=31.5,
                observer_longitude_deg=-99.4,
                moon_direction_equatorial=moon_direction,
                moon_to_sun_direction_equatorial=(-0.4, 0.2, 0.8),
            )

        _assert_matrix_orthonormal(self, state.view_to_moon_matrix)
        self.assertEqual(provider.compute_fallback_count, 1)
        self.assertIn("missing lunar frame coverage", provider.last_compute_error)

    def test_lunar_orientation_comparison_reports_finite_spice_approx_deltas(self) -> None:

        with tempfile.TemporaryDirectory() as temp_dir:

            kernel_path = Path(temp_dir) / "moon.tpc"
            kernel_path.write_text("fake kernel", encoding="utf-8")
            provider = LunarOrientationSpiceProvider((kernel_path,), spice_module=_FakeSpiceModule(_rotation_z(0.1)))

            results = compare_lunar_orientation_providers(provider)

        self.assertGreaterEqual(len(results), 3)
        for result in results:
            self.assertTrue(math.isfinite(result.max_body_axis_delta_deg))
            self.assertTrue(math.isfinite(result.libration_longitude_delta_deg))
            self.assertGreaterEqual(result.max_body_axis_delta_deg, 0.0)

    def test_moon_ephemeris_default_provider_falls_back_without_startup_failure(self) -> None:

        ephemeris = MoonEphemeris()

        self.assertIn(ephemeris.orientation_provider_diagnostics.active_provider, {"approx", "spice"})
        self.assertTrue(ephemeris.orientation_provider_diagnostics.diagnostic)
        ephemeris._cache.shutdown()

    def test_moon_renderer_interface_does_not_depend_on_orientation_provider(self) -> None:

        signature = inspect.signature(MoonRendererGL.draw)

        self.assertNotIn("orientation_provider", signature.parameters)
        self.assertNotIn("lunar_orientation", signature.parameters)

    def test_moon_phase_geometry_is_unchanged_by_orientation_provider(self) -> None:

        observation_time = datetime(2026, 5, 25, 12, 2, 30, tzinfo=UTC)
        first_ephemeris = MoonEphemeris(orientation_provider=_StaticOrientationProvider(_orientation_state(0.0)))
        second_ephemeris = MoonEphemeris(orientation_provider=_StaticOrientationProvider(_orientation_state(1.7)))

        first_state = first_ephemeris.precise_state_at(observation_time, 31.5475, -99.3819, 472.0)
        second_state = second_ephemeris.precise_state_at(observation_time, 31.5475, -99.3819, 472.0)

        self.assertAlmostEqual(first_state.phase_angle_deg, second_state.phase_angle_deg, places=9)
        self.assertAlmostEqual(first_state.illuminated_fraction, second_state.illuminated_fraction, places=12)
        self.assertNotAlmostEqual(first_state.libration_longitude_rad, second_state.libration_longitude_rad, places=3)
        first_ephemeris._cache.shutdown()
        second_ephemeris._cache.shutdown()

    def test_moon_ephemeris_caches_precise_samples_and_interpolates(self) -> None:

        calls: list[datetime] = []

        def resolver(observation_time: datetime, _lat: float, _lon: float, _elev: float) -> MoonState:

            calls.append(observation_time)
            minute_offset = (observation_time.minute * 60 + observation_time.second) / 60.0
            return _fake_state(observation_time, altitude_deg=10.0 + minute_offset)

        cache = MoonCache(executor=_ImmediateExecutor())  # type: ignore[arg-type]
        ephemeris = MoonEphemeris(cache, sample_interval_seconds=300, precise_state_resolver=resolver)
        first_time = datetime(2026, 5, 25, 12, 2, 30, tzinfo=UTC)
        second_time = datetime(2026, 5, 25, 12, 2, 45, tzinfo=UTC)

        first_state = ephemeris.state_at(first_time, 31.0, -99.0, 400.0)
        second_state = ephemeris.state_at(second_time, 31.0, -99.0, 400.0)

        self.assertEqual(len(calls), 2)
        self.assertEqual(cache.state_sample_count, 2)
        self.assertAlmostEqual(first_state.altitude_deg, 12.5, places=3)
        self.assertGreater(second_state.altitude_deg, first_state.altitude_deg)

    def test_moon_ephemeris_interpolates_fractional_simulation_seconds(self) -> None:

        start_time = datetime(2026, 5, 25, 12, 0, tzinfo=UTC)

        def resolver(observation_time: datetime, _lat: float, _lon: float, _elev: float) -> MoonState:

            return _fake_state(observation_time, altitude_deg=(observation_time - start_time).total_seconds())

        cache = MoonCache(executor=_ImmediateExecutor())  # type: ignore[arg-type]
        ephemeris = MoonEphemeris(cache, sample_interval_seconds=300, precise_state_resolver=resolver)
        state = ephemeris.state_at(start_time + timedelta(seconds=150, milliseconds=250), 31.0, -99.0, 400.0)

        self.assertAlmostEqual(state.altitude_deg, 150.25, places=6)
        cache.shutdown()

    def test_async_moon_ephemeris_sampler_interpolates_ready_brackets_and_adapts_interval(self) -> None:

        start_time = datetime(2026, 5, 25, 12, 0, tzinfo=UTC)

        def resolver(observation_time: datetime, _lat: float, _lon: float, _elev: float) -> MoonState:

            return _fake_state(observation_time, altitude_deg=(observation_time - start_time).total_seconds())

        cache = MoonCache(executor=_ImmediateExecutor())  # type: ignore[arg-type]
        ephemeris = MoonEphemeris(cache, precise_state_resolver=resolver)
        sampler = MoonAsyncEphemerisSampler(ephemeris, executor=_ImmediateExecutor())  # type: ignore[arg-type]
        state = sampler.state_at_nonblocking(
            start_time + timedelta(seconds=15, milliseconds=250),
            31.0,
            -99.0,
            400.0,
            moon_diameter_px=800.0,
            pixels_per_degree=500.0,
        )

        self.assertIsNotNone(state)
        assert state is not None
        self.assertAlmostEqual(state.altitude_deg, 15.25, places=6)
        self.assertEqual(sampler.diagnostics.update_interval_seconds, 30)
        self.assertEqual(sampler.diagnostics.cache_status, "interpolated")
        _assert_finite_moon_state(self, state)
        sampler.shutdown()
        cache.shutdown()

    def test_async_moon_ephemeris_sampler_invalidates_brackets_after_large_scrub(self) -> None:

        start_time = datetime(2026, 5, 25, 12, 0, tzinfo=UTC)
        cache = MoonCache(executor=_ImmediateExecutor())  # type: ignore[arg-type]
        ephemeris = MoonEphemeris(cache, precise_state_resolver=lambda time, _lat, _lon, _elev: _fake_state(time, 20.0))
        sampler = MoonAsyncEphemerisSampler(ephemeris, executor=_ImmediateExecutor())  # type: ignore[arg-type]

        sampler.state_at_nonblocking(start_time, 31.0, -99.0, 400.0)
        sampler.state_at_nonblocking(start_time + timedelta(hours=3), 31.0, -99.0, 400.0)

        self.assertTrue(sampler.diagnostics.invalidated_due_to_scrub)
        sampler.shutdown()
        cache.shutdown()

    def test_moon_orientation_interpolation_is_wrap_safe_during_fast_scrub(self) -> None:

        def resolver(observation_time: datetime, _lat: float, _lon: float, _elev: float) -> MoonState:

            angle = math.pi - 0.08 if observation_time.minute < 5 else -math.pi + 0.08
            orientation = _orientation_state(angle)
            return replace(
                _fake_state(observation_time, altitude_deg=30.0),
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

        cache = MoonCache(executor=_ImmediateExecutor())  # type: ignore[arg-type]
        ephemeris = MoonEphemeris(cache, sample_interval_seconds=300, precise_state_resolver=resolver)
        previous_longitude: float | None = None
        previous_matrix: tuple[tuple[float, float, float], tuple[float, float, float], tuple[float, float, float]] | None = None

        start_time = datetime(2026, 5, 25, 12, 0, tzinfo=UTC)
        for step in range(11):
            state = ephemeris.state_at(start_time + timedelta(seconds=step * 30), 31.0, -99.0, 400.0)
            self.assertTrue(math.isfinite(state.libration_longitude_rad))
            _assert_matrix_orthonormal(self, state.view_to_moon_matrix)
            if previous_longitude is not None:
                self.assertLess(_angle_distance_rad(state.libration_longitude_rad, previous_longitude), 0.04)
            if previous_matrix is not None:
                self.assertGreater(sum(previous_matrix[0][axis] * state.view_to_moon_matrix[0][axis] for axis in range(3)), 0.99)
            previous_longitude = state.libration_longitude_rad
            previous_matrix = state.view_to_moon_matrix

        cache.shutdown()

    def test_moon_time_scrub_over_lunar_month_has_no_nan_or_orientation_jump(self) -> None:

        start_time = datetime(2026, 5, 1, 0, 0, tzinfo=UTC)
        lunar_month_days = 29.530588

        def resolver(observation_time: datetime, _lat: float, _lon: float, _elev: float) -> MoonState:

            elapsed_days = (observation_time - start_time).total_seconds() / 86400.0
            phase_cycle = (elapsed_days / lunar_month_days) % 1.0
            phase_angle = phase_cycle * 360.0
            illuminated = 0.5 * (1.0 - math.cos(math.radians(phase_angle)))
            wrapped_angle = ((phase_cycle * 2.0 * math.pi + math.pi) % (2.0 * math.pi)) - math.pi
            orientation = _orientation_state(wrapped_angle)
            return replace(
                _fake_state(observation_time, altitude_deg=12.0 + 45.0 * math.sin(phase_cycle * 2.0 * math.pi), azimuth_deg=(90.0 + elapsed_days * 11.0) % 360.0),
                phase_angle_deg=phase_angle,
                illuminated_fraction=illuminated,
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

        cache = MoonCache(executor=_ImmediateExecutor())  # type: ignore[arg-type]
        ephemeris = MoonEphemeris(cache, sample_interval_seconds=6 * 3600, precise_state_resolver=resolver)
        previous_longitude: float | None = None

        for step in range(0, 30 * 4 + 1):
            state = ephemeris.state_at(start_time + timedelta(hours=step * 6), 31.0, -99.0, 400.0)
            _assert_finite_moon_state(self, state)
            _assert_matrix_orthonormal(self, state.view_to_moon_matrix)
            atmosphere = MoonAtmosphereModel.atmosphere_for_altitude(state.altitude_deg)
            self.assertTrue(math.isfinite(atmosphere.extinction))
            _assert_finite_vector(self, atmosphere.tint_rgb)
            if previous_longitude is not None:
                self.assertLess(_angle_distance_rad(state.libration_longitude_rad, previous_longitude), 0.08)
            previous_longitude = state.libration_longitude_rad

        cache.shutdown()

    def test_moon_orientation_is_valid_for_phase_and_horizon_smoke_cases(self) -> None:

        ephemeris = MoonEphemeris()
        requested_phases = {"crescent", "first-quarter", "full", "horizon"}
        cases = [case for case in DEFAULT_MOON_VISUAL_SMOKE_CASES if case.expected_phase in requested_phases]

        for case in cases:
            state = ephemeris.precise_state_at(case.observation_time_utc, case.latitude_deg, case.longitude_deg, 0.0)
            _assert_matrix_orthonormal(self, state.equatorial_to_moon_matrix)
            _assert_matrix_orthonormal(self, state.view_to_moon_matrix)
            self.assertTrue(math.isfinite(state.lunar_north_position_angle_rad), case.label)
            self.assertTrue(math.isfinite(state.parallactic_angle_rad), case.label)
            self.assertTrue(-math.pi <= state.libration_longitude_rad <= math.pi, case.label)
            self.assertTrue(-math.pi / 2.0 <= state.libration_latitude_rad <= math.pi / 2.0, case.label)

        ephemeris._cache.shutdown()

    def test_moon_ephemeris_validation_compares_cached_state_to_astropy_direct(self) -> None:

        case = MoonEphemerisValidationCase(
            "validation sample",
            datetime(2026, 5, 25, 12, 2, 30, tzinfo=UTC),
            31.5475,
            -99.3819,
            472.0,
        )

        result = validate_moon_ephemeris_against_astropy(
            cases=(
                case,  # type: ignore[arg-type]
            ),
            sample_interval_seconds=300,
        )[0]

        self.assertLess(result.altitude_error_arcsec, 900.0)
        self.assertLess(result.azimuth_error_arcsec, 900.0)
        self.assertLess(result.angular_diameter_error_arcsec, 1.0)
        self.assertLess(result.illuminated_fraction_error, 0.01)

    def test_moon_cache_builds_texture_lods_from_color_texture_without_blocking_request(self) -> None:

        with tempfile.TemporaryDirectory() as temp_dir:

            texture_path = Path(temp_dir) / "moon_color.png"
            image = QImage(64, 32, QImage.Format.Format_RGB888)
            image.fill(QColor("#808080"))
            self.assertTrue(image.save(str(texture_path)))
            cache = MoonCache(
                Path(temp_dir),
                color_texture_path=texture_path,
                lod_widths=(16, 32, 64),
                executor=_ImmediateExecutor(),  # type: ignore[arg-type]
            )

            started = cache.request_texture_lods()
            lod = cache.best_available_texture_lod(120.0)

            self.assertTrue(started)
            self.assertIsNotNone(lod)
            assert lod is not None
            self.assertEqual((lod.width, lod.height), (64, 32))
            self.assertFalse(cache.has_pending_texture_work())
            cache.shutdown()

    def test_moon_cache_lod_selection_scales_with_apparent_pixel_diameter(self) -> None:

        cache = MoonCache(lod_widths=(512, 1024, 2048, 4096, 8192), executor=_ImmediateExecutor())  # type: ignore[arg-type]

        self.assertEqual(cache.texture_lod_for_diameter_px(24.0), 512)
        self.assertEqual(cache.texture_lod_for_diameter_px(80.0), 1024)
        self.assertEqual(cache.texture_lod_for_diameter_px(150.0), 2048)
        self.assertEqual(cache.texture_lod_for_diameter_px(320.0), 4096)
        self.assertEqual(cache.texture_lod_for_diameter_px(900.0), 8192)
        cache.shutdown()

    def test_moon_cache_missing_texture_fails_once_without_requeueing(self) -> None:

        with tempfile.TemporaryDirectory() as temp_dir:

            cache = MoonCache(
                Path(temp_dir),
                color_texture_path=Path(temp_dir) / "missing-moon.png",
                lod_widths=(16,),
                executor=_ImmediateExecutor(),  # type: ignore[arg-type]
            )

            self.assertTrue(cache.request_texture_lods())
            self.assertIsNone(cache.best_available_texture_lod(32.0))
            self.assertIn("unavailable", cache.texture_lod_error)
            self.assertFalse(cache.request_texture_lods())
            cache.shutdown()

    def test_moon_cache_unreadable_albedo_fails_safely_without_crashing(self) -> None:

        with tempfile.TemporaryDirectory() as temp_dir:

            texture_path = Path(temp_dir) / "not-an-image.tif"
            texture_path.write_bytes(b"not a readable image")
            cache = MoonCache(
                Path(temp_dir),
                color_texture_path=texture_path,
                lod_widths=(16,),
                executor=_ImmediateExecutor(),  # type: ignore[arg-type]
            )

            self.assertTrue(cache.request_texture_lods())
            self.assertIsNone(cache.best_available_texture_lod(32.0))
            self.assertIn("unavailable", cache.texture_lod_error)
            self.assertFalse(cache.request_texture_lods())
            cache.shutdown()

    def test_moon_cache_missing_normal_source_falls_back_cleanly(self) -> None:

        with tempfile.TemporaryDirectory() as temp_dir:

            cache = MoonCache(
                Path(temp_dir),
                displacement_texture_path=Path(temp_dir) / "missing-ldem.png",
                lod_widths=(16,),
                executor=_ImmediateExecutor(),  # type: ignore[arg-type]
            )

            self.assertFalse(cache.request_normal_texture_lods())
            self.assertIsNone(cache.best_available_normal_texture_lod(32.0))
            self.assertIn("unavailable", cache.normal_texture_lod_error)
            cache.shutdown()

    def test_moon_cache_missing_ldem_uses_explicit_normal_map_when_available(self) -> None:

        with tempfile.TemporaryDirectory() as temp_dir:

            normal_path = Path(temp_dir) / "moon_normal.png"
            normal_image = QImage(32, 16, QImage.Format.Format_RGB888)
            normal_image.fill(QColor(128, 128, 255))
            self.assertTrue(normal_image.save(str(normal_path)))
            cache = MoonCache(
                Path(temp_dir),
                normal_texture_path=normal_path,
                displacement_texture_path=Path(temp_dir) / "missing-ldem.tif",
                lod_widths=(16, 32),
                executor=_ImmediateExecutor(),  # type: ignore[arg-type]
            )

            self.assertTrue(cache.request_normal_texture_lods())
            lod = cache.best_available_normal_texture_lod(90.0)

            self.assertIsNotNone(lod)
            assert lod is not None
            self.assertEqual(lod.width, 32)
            self.assertEqual(lod.cache_key[2], "normal")
            cache.shutdown()

    def test_moon_cache_unreadable_normal_source_fails_safely(self) -> None:

        with tempfile.TemporaryDirectory() as temp_dir:

            normal_path = Path(temp_dir) / "bad-normal.png"
            normal_path.write_bytes(b"not a readable normal image")
            cache = MoonCache(
                Path(temp_dir),
                normal_texture_path=normal_path,
                displacement_texture_path=Path(temp_dir) / "missing-ldem.tif",
                lod_widths=(16,),
                executor=_ImmediateExecutor(),  # type: ignore[arg-type]
            )

            self.assertTrue(cache.request_normal_texture_lods())
            self.assertIsNone(cache.best_available_normal_texture_lod(32.0))
            self.assertIn("unavailable", cache.normal_texture_lod_error)
            self.assertFalse(cache.request_normal_texture_lods())
            cache.shutdown()

    def test_moon_cache_builds_normal_lods_from_height_map(self) -> None:

        with tempfile.TemporaryDirectory() as temp_dir:

            height_path = Path(temp_dir) / "moon_height.png"
            image = QImage(64, 32, QImage.Format.Format_Grayscale8)
            for y_pos in range(32):
                for x_pos in range(64):
                    image.setPixelColor(x_pos, y_pos, QColor((x_pos * 4) % 255, (x_pos * 4) % 255, (x_pos * 4) % 255))
            self.assertTrue(image.save(str(height_path)))
            cache = MoonCache(
                Path(temp_dir),
                displacement_texture_path=height_path,
                lod_widths=(16, 32, 64),
                executor=_ImmediateExecutor(),  # type: ignore[arg-type]
            )

            self.assertTrue(cache.request_normal_texture_lods())
            lod = cache.best_available_normal_texture_lod(900.0)

            self.assertIsNotNone(lod)
            assert lod is not None
            self.assertEqual((lod.width, lod.height), (64, 32))
            self.assertTrue(lod.label.startswith("normal-"))
            self.assertFalse(cache.has_pending_normal_texture_work())
            cache.shutdown()

    def test_moon_cache_normal_lod_pending_does_not_block_albedo_draw_readiness(self) -> None:

        with tempfile.TemporaryDirectory() as temp_dir:

            height_path = Path(temp_dir) / "moon_height.png"
            image = QImage(16, 8, QImage.Format.Format_Grayscale8)
            image.fill(QColor("#808080"))
            self.assertTrue(image.save(str(height_path)))
            executor = _DeferredExecutor()
            cache = MoonCache(
                Path(temp_dir),
                displacement_texture_path=height_path,
                lod_widths=(16,),
                executor=executor,  # type: ignore[arg-type]
            )

            self.assertTrue(cache.request_normal_texture_lods())
            self.assertTrue(cache.has_pending_normal_texture_work())
            self.assertIsNone(cache.best_available_normal_texture_lod(32.0))
            cache.shutdown()

    def test_moon_cache_accepts_partially_prepared_normal_lods(self) -> None:

        with tempfile.TemporaryDirectory() as temp_dir:

            height_path = Path(temp_dir) / "moon_height.png"
            image = QImage(16, 8, QImage.Format.Format_Grayscale8)
            image.fill(QColor("#808080"))
            self.assertTrue(image.save(str(height_path)))
            executor = _DeferredExecutor()
            cache = MoonCache(
                Path(temp_dir),
                displacement_texture_path=height_path,
                lod_widths=(16, 32),
                executor=executor,  # type: ignore[arg-type]
            )

            self.assertTrue(cache.request_normal_texture_lods())
            assert executor.future is not None
            partial_image = QImage(16, 8, QImage.Format.Format_RGB888)
            partial_image.fill(QColor(128, 128, 255))
            executor.future.set_result(
                (
                    MoonTextureLod(
                        label="normal-16w",
                        width=16,
                        height=8,
                        image=partial_image,
                        cache_key=("normal", "partial", 16),
                    ),
                )
            )

            lod = cache.best_available_normal_texture_lod(900.0)

            self.assertIsNotNone(lod)
            assert lod is not None
            self.assertEqual(lod.width, 16)
            self.assertFalse(cache.has_pending_normal_texture_work())
            cache.shutdown()

    def test_moon_cache_lod_thresholds_are_monotonic_and_deterministic_under_zoom(self) -> None:

        cache = MoonCache(lod_widths=(512, 1024, 2048, 4096, 8192), executor=_ImmediateExecutor())  # type: ignore[arg-type]
        diameters = (8.0, 24.0, 48.0, 48.1, 80.0, 96.0, 96.1, 150.0, 192.0, 192.1, 320.0, 384.0, 384.1, 900.0)
        first_pass = [cache.texture_lod_for_diameter_px(value) for value in diameters]
        second_pass = [cache.texture_lod_for_diameter_px(value) for value in diameters]

        self.assertEqual(first_pass, second_pass)
        self.assertEqual(first_pass, sorted(first_pass))
        cache.shutdown()

    def test_moon_renderer_sets_normal_map_shader_flags(self) -> None:

        renderer = MoonRendererGL()
        uniform_names = _moon_renderer_uniform_names()
        renderer._uniform_locations = {name: index for index, name in enumerate(uniform_names)}
        functions = _FakeUniformFunctions(renderer._uniform_locations)
        draw_state = MoonDrawState(
            viewport_width_px=640,
            viewport_height_px=420,
            center_x_px=320.0,
            center_y_px=210.0,
            radius_px=12.0,
            opacity=1.0,
            sun_direction_moon_local=(0.0, 0.0, 1.0),
            view_to_moon_matrix=((1.0, 0.0, 0.0), (0.0, 1.0, 0.0), (0.0, 0.0, 1.0)),
            selected_lod_label="512w",
            terrain_normal_strength=0.42,
            terrain_normal_strength_global=0.12,
            terrain_normal_strength_terminator=0.42,
            tiled_normal_strength=0.07,
            tiled_terminator_normal_strength=0.18,
            tiled_normal_fade_factor=0.35,
            tiled_normal_max_slope=0.25,
            polar_cap_normal_strength=0.018,
            polar_cap_blend_start_lat_deg=61.0,
            polar_cap_blend_end_lat_deg=79.0,
            polar_cap_blend_curve_power=1.4,
            polar_cap_normal_blend_start_lat_deg=79.5,
            polar_cap_normal_blend_end_lat_deg=86.5,
            polar_cap_albedo_sharpness=0.31,
            polar_cap_local_contrast=1.12,
            polar_cap_boundary_match_strength=0.42,
            polar_cap_core_match_strength=0.67,
            earthshine_strength=0.05,
            atmosphere_extinction=0.8,
            atmosphere_tint_rgb=(1.0, 0.92, 0.78),
        )

        renderer._set_uniform_values(functions, draw_state, has_normal_map=True, has_global_normal_map=True)  # type: ignore[arg-type]

        self.assertEqual(functions.values["u_has_moon_normal_map"], 1)
        self.assertEqual(functions.values["u_has_global_moon_normal_map"], 1)
        self.assertEqual(functions.values["u_moon_normal_texture"], 1)
        self.assertEqual(functions.values["u_moonTileAlbedoCount"], 0)
        self.assertAlmostEqual(cast(float, functions.values["u_terrain_normal_strength"]), 0.42)
        self.assertAlmostEqual(cast(float, functions.values["u_terrain_normal_strength_global"]), 0.12)
        self.assertAlmostEqual(cast(float, functions.values["u_terrain_normal_strength_terminator"]), 0.42)
        self.assertAlmostEqual(cast(float, functions.values["u_tiled_normal_strength"]), 0.07)
        self.assertAlmostEqual(cast(float, functions.values["u_tiled_terminator_normal_strength"]), 0.18)
        self.assertAlmostEqual(cast(float, functions.values["u_tiled_normal_fade_factor"]), 0.35)
        self.assertAlmostEqual(cast(float, functions.values["u_tiled_normal_max_slope"]), 0.25)
        self.assertAlmostEqual(cast(float, functions.values["u_polarCapNormalStrength"]), 0.018)
        self.assertAlmostEqual(cast(float, functions.values["u_polarCapBlendStartLatDeg"]), 61.0)
        self.assertAlmostEqual(cast(float, functions.values["u_polarCapBlendEndLatDeg"]), 79.0)
        self.assertAlmostEqual(cast(float, functions.values["u_polarCapBlendCurvePower"]), 1.4)
        self.assertAlmostEqual(cast(float, functions.values["u_polarCapNormalBlendStartLatDeg"]), 79.5)
        self.assertAlmostEqual(cast(float, functions.values["u_polarCapNormalBlendEndLatDeg"]), 86.5)
        self.assertAlmostEqual(cast(float, functions.values["u_polarCapAlbedoSharpness"]), 0.31)
        self.assertAlmostEqual(cast(float, functions.values["u_polarCapLocalContrast"]), 1.12)
        self.assertAlmostEqual(cast(float, functions.values["u_polarCapBoundaryMatchStrength"]), 0.42)
        self.assertAlmostEqual(cast(float, functions.values["u_polarCapCoreMatchStrength"]), 0.67)
        self.assertEqual(functions.values["u_atmosphere_tint_rgb"], (1.0, 0.92, 0.78))
        self.assertEqual(functions.values["u_debug_render_mode"], 0)
        self.assertEqual(functions.values["u_disable_tone_mapping"], 0)
        self.assertEqual(functions.values["u_force_tiled_texture_only"], 0)

        lighting_draw_state = replace(draw_state, debug_render_mode="lighting_only")

        renderer._set_uniform_values(functions, lighting_draw_state, has_normal_map=True, has_global_normal_map=True)  # type: ignore[arg-type]

        self.assertEqual(functions.values["u_debug_render_mode"], 2)

        renderer._set_uniform_values(functions, draw_state, has_normal_map=False)  # type: ignore[arg-type]

        self.assertEqual(functions.values["u_has_moon_normal_map"], 0)
        self.assertAlmostEqual(cast(float, functions.values["u_terrain_normal_strength"]), 0.0)
        self.assertAlmostEqual(cast(float, functions.values["u_terrain_normal_strength_global"]), 0.0)
        self.assertAlmostEqual(cast(float, functions.values["u_terrain_normal_strength_terminator"]), 0.0)
        self.assertAlmostEqual(cast(float, functions.values["u_tiled_normal_strength"]), 0.0)
        self.assertAlmostEqual(cast(float, functions.values["u_tiled_normal_fade_factor"]), 0.0)

    def test_moon_renderer_shader_uses_central_visual_settings_constants(self) -> None:

        shader = MoonRendererGL._FRAGMENT_SHADER_SOURCE

        self.assertIn("MOON_TERMINATOR_SOFT_START", shader)
        self.assertIn(f"{DEFAULT_MOON_VISUAL_SETTINGS.terrain_blend_cap:.6f}", shader)
        self.assertIn(f"{DEFAULT_MOON_VISUAL_SETTINGS.earthshine_max:.6f}", shader)
        self.assertIn(f"{DEFAULT_MOON_VISUAL_SETTINGS.tone_gamma:.6f}", shader)
        self.assertIn("u_debug_render_mode", shader)
        self.assertIn(f"{DEFAULT_MOON_VISUAL_SETTINGS.tone_contrast:.6f}", shader)
        self.assertIn(f"{DEFAULT_MOON_VISUAL_SETTINGS.tone_saturation:.6f}", shader)
        self.assertIn("u_terrain_normal_strength_global", shader)
        self.assertIn("u_terrain_normal_strength_terminator", shader)
        self.assertIn("u_tiled_normal_fade_factor", shader)
        self.assertIn("clampMoonTiledNormalSlope", shader)
        self.assertIn("MOON_TERMINATOR_NORMAL_MIN_FACTOR", shader)
        self.assertIn("moonPolarNormalFadeFactor", shader)
        self.assertIn("sampleMoonPolarSafeGlobalAlbedo", shader)
        self.assertIn("moonSeamProximityFactor", shader)
        self.assertIn("u_disable_tone_mapping", shader)
        self.assertIn("u_force_tiled_texture_only", shader)
        self.assertIn(f"{DEFAULT_MOON_VISUAL_SETTINGS.terminator_band_width:.6f}", shader)
        self.assertIn(f"{DEFAULT_MOON_VISUAL_SETTINGS.earthshine_directional_gamma:.6f}", shader)
        self.assertIn(f"{DEFAULT_MOON_VISUAL_SETTINGS.tone_color_preservation:.6f}", shader)

    def test_terrain_normal_influence_is_small_away_from_terminator(self) -> None:

        self.assertLess(MoonRendererGL.terrain_normal_blend_weight(0.96, 0.32), 0.02)
        self.assertLess(MoonRendererGL.terrain_normal_blend_weight(-0.45, 0.32), 0.02)
        self.assertGreater(MoonRendererGL.terrain_normal_blend_weight(0.08, 0.32), 0.12)

    def test_moon_atmosphere_values_are_finite_and_bounded(self) -> None:

        for altitude_deg in (-10.0, -2.0, 0.0, 5.0, 30.0, 90.0):
            atmosphere = MoonAtmosphereModel.atmosphere_for_altitude(altitude_deg)
            self.assertTrue(math.isfinite(atmosphere.extinction))
            self.assertGreaterEqual(atmosphere.extinction, 0.0)
            self.assertLessEqual(atmosphere.extinction, 1.0)
            for component in atmosphere.tint_rgb:
                self.assertTrue(math.isfinite(component))
                self.assertGreaterEqual(component, 0.0)
                self.assertLessEqual(component, 1.0)

    def test_moon_visual_settings_bounds_are_ordered_and_conservative(self) -> None:

        settings = DEFAULT_MOON_VISUAL_SETTINGS

        self.assertLess(settings.terminator_soft_start, settings.terminator_soft_end)
        self.assertLess(settings.terrain_terminator_start, settings.terrain_terminator_peak_end)
        self.assertLess(settings.terrain_terminator_peak_end, settings.terrain_terminator_fade_end)
        self.assertLess(settings.terrain_terminator_fade_start, settings.terrain_terminator_fade_end)
        self.assertGreater(settings.earthshine_floor_default, 0.0)
        self.assertLessEqual(settings.earthshine_floor_default, settings.earthshine_max)
        self.assertGreater(settings.tone_exposure, 0.0)
        self.assertGreater(settings.tone_highlight_knee, 0.0)
        self.assertGreater(settings.tone_highlight_compression, 0.0)
        self.assertGreater(settings.lighting_highlight_knee, 0.0)
        self.assertGreater(settings.lighting_highlight_compression, 0.0)
        self.assertGreater(settings.albedo_brightness, 0.0)
        self.assertGreater(settings.albedo_contrast, 0.0)
        self.assertGreater(settings.albedo_saturation_retention, 0.0)
        self.assertGreater(settings.tone_contrast, 0.0)
        self.assertGreater(settings.tone_saturation, 0.0)
        self.assertLess(settings.tiled_normal_strength, settings.global_normal_strength)
        self.assertLess(settings.tiled_terminator_normal_strength, settings.global_terminator_normal_strength)
        self.assertGreater(settings.tiled_normal_max_slope, 0.0)
        self.assertLess(settings.tiled_normal_fade_start_offset_px, settings.tiled_normal_fade_end_offset_px)
        self.assertLess(settings.polar_normal_fade_start_lat_deg, settings.polar_normal_fade_end_lat_deg)
        self.assertLess(settings.polar_albedo_fade_start_lat_deg, settings.polar_albedo_fade_end_lat_deg)
        self.assertTrue(settings.polar_use_analytic_normal)
        self.assertLess(settings.polar_cap_blend_start_lat_deg, settings.polar_cap_blend_end_lat_deg)
        self.assertGreater(settings.polar_cap_normal_strength, 0.0)
        self.assertLessEqual(settings.polar_cap_blend_start_lat_deg, settings.polar_cap_normal_blend_start_lat_deg)
        self.assertLess(settings.polar_cap_normal_blend_start_lat_deg, settings.polar_cap_normal_blend_end_lat_deg)
        self.assertGreater(settings.polar_cap_blend_curve_power, 0.0)
        self.assertGreaterEqual(settings.polar_cap_albedo_sharpness, 0.0)
        self.assertGreater(settings.polar_cap_local_contrast, 0.0)
        self.assertGreaterEqual(settings.polar_cap_boundary_match_strength, 0.0)
        self.assertLessEqual(settings.polar_cap_boundary_match_strength, 1.0)
        self.assertGreaterEqual(settings.polar_cap_core_match_strength, 0.0)
        self.assertLessEqual(settings.polar_cap_core_match_strength, 1.0)
        self.assertLessEqual(settings.tone_highlight_knee, 1.0)
        self.assertGreaterEqual(settings.tone_gamma, 0.85)
        self.assertLessEqual(settings.tone_gamma, 1.1)

    def test_moon_highlight_compression_values_are_finite_bounded_and_monotonic(self) -> None:

        inputs = (0.0, 0.18, 0.65, 0.9, 1.2, 1.8, 3.0)
        outputs = [MoonRendererGL.highlight_compression_value(value) for value in inputs]

        for output in outputs:
            self.assertTrue(math.isfinite(output))
            self.assertGreaterEqual(output, 0.0)
            self.assertLessEqual(output, 1.5)

        self.assertEqual(outputs, sorted(outputs))
        self.assertLess(outputs[-1] - outputs[-2], inputs[-1] - inputs[-2])

        tone_mapped = MoonRendererGL.tone_mapped_color((1.9, 1.6, 1.2))
        for component in tone_mapped:
            self.assertTrue(math.isfinite(component))
            self.assertGreaterEqual(component, 0.0)
            self.assertLessEqual(component, 1.0)

        neutral_tone = MoonRendererGL.tone_mapped_color((0.52, 0.52, 0.52))
        self.assertAlmostEqual(neutral_tone[0], neutral_tone[1], delta=1.0e-6)
        self.assertAlmostEqual(neutral_tone[1], neutral_tone[2], delta=1.0e-6)

        warm_tone = MoonRendererGL.tone_mapped_color((0.64, 0.54, 0.42))
        self.assertGreater(warm_tone[0], warm_tone[1])
        self.assertGreater(warm_tone[1], warm_tone[2])

    def test_moon_albedo_remap_is_finite_bounded_and_improves_separation(self) -> None:

        maria_sample = (0.28, 0.29, 0.31)
        highland_sample = (0.66, 0.65, 0.63)
        remapped_maria = MoonRendererGL.albedo_remapped_color(maria_sample)
        remapped_highland = MoonRendererGL.albedo_remapped_color(highland_sample)

        for sample in (remapped_maria, remapped_highland):
            for component in sample:
                self.assertTrue(math.isfinite(component))
                self.assertGreaterEqual(component, 0.0)
                self.assertLessEqual(component, 1.0)

        maria_luminance = 0.2126 * remapped_maria[0] + 0.7152 * remapped_maria[1] + 0.0722 * remapped_maria[2]
        highland_luminance = 0.2126 * remapped_highland[0] + 0.7152 * remapped_highland[1] + 0.0722 * remapped_highland[2]
        self.assertGreater(highland_luminance - maria_luminance, 0.34)

    def test_moon_lighting_scalar_is_bounded_and_not_uniform_white(self) -> None:

        samples = [MoonRendererGL.lighting_scalar_value(value) for value in (0.0, 0.2, 0.5, 0.8, 1.0)]

        for sample in samples:
            self.assertTrue(math.isfinite(sample))
            self.assertGreaterEqual(sample, 0.0)
            self.assertLessEqual(sample, 1.0)

        self.assertEqual(samples, sorted(samples))
        self.assertLess(samples[-1], 0.9)
        self.assertGreater(samples[-1] - samples[1], 0.18)

    def test_moon_debug_render_mode_codes_are_stable(self) -> None:

        self.assertEqual(MoonRendererGL.debug_render_mode_code("normal"), 0)
        self.assertEqual(MoonRendererGL.debug_render_mode_code("albedo_only"), 1)
        self.assertEqual(MoonRendererGL.debug_render_mode_code("lighting_only"), 2)
        self.assertEqual(MoonRendererGL.debug_render_mode_code("normal_visualization"), 3)
        self.assertEqual(MoonRendererGL.debug_render_mode_code("uv_debug"), 4)
        self.assertEqual(MoonRendererGL.debug_render_mode_code("tile_source_route"), 5)
        self.assertEqual(MoonRendererGL.debug_render_mode_code("global_tiled_difference"), 6)
        self.assertEqual(MoonRendererGL.debug_render_mode_code("polar_cap_route"), 7)
        self.assertEqual(MoonRendererGL.debug_render_mode_code("polar_cap_footprint"), 8)
        self.assertEqual(MoonRendererGL.debug_render_mode_code("unexpected"), 0)

    def test_moon_earthshine_values_are_finite_and_bounded(self) -> None:

        strengths = [MoonEarthshineModel.earthshine_strength_for_phase(value) for value in (0.0, 0.05, 0.5, 1.0)]

        for strength in strengths:
            self.assertTrue(math.isfinite(strength))
            self.assertGreaterEqual(strength, 0.0)
            self.assertLessEqual(strength, DEFAULT_MOON_VISUAL_SETTINGS.earthshine_max)
        self.assertGreater(strengths[0], strengths[-1])
        self.assertEqual(MoonEarthshineModel.earthshine_strength_for_phase(0.1, disabled=True), 0.0)

    def test_high_zoom_terrain_strength_boost_is_bounded(self) -> None:

        settings = DEFAULT_MOON_VISUAL_SETTINGS
        low_zoom = MoonRendererGL.resolved_terrain_normal_strength(settings.terrain_normal_strength_default, 120.0)
        high_zoom = MoonRendererGL.resolved_terrain_normal_strength(settings.terrain_normal_strength_default, 1400.0)

        self.assertGreaterEqual(low_zoom, settings.terrain_normal_strength_default)
        self.assertGreater(high_zoom, low_zoom)
        self.assertLessEqual(high_zoom, 1.0)

    def test_high_zoom_split_terrain_strength_components_are_bounded(self) -> None:

        settings = DEFAULT_MOON_VISUAL_SETTINGS
        low_zoom_global, low_zoom_terminator = MoonRendererGL.resolved_terrain_normal_strength_components(
            settings.terrain_normal_strength_global,
            settings.terrain_normal_strength_terminator,
            120.0,
        )
        high_zoom_global, high_zoom_terminator = MoonRendererGL.resolved_terrain_normal_strength_components(
            settings.terrain_normal_strength_global,
            settings.terrain_normal_strength_terminator,
            1400.0,
        )

        self.assertGreaterEqual(low_zoom_global, settings.terrain_normal_strength_global)
        self.assertGreaterEqual(low_zoom_terminator, settings.terrain_normal_strength_terminator)
        self.assertGreater(high_zoom_global, low_zoom_global)
        self.assertGreater(high_zoom_terminator, low_zoom_terminator)
        self.assertLessEqual(high_zoom_global, 1.0)
        self.assertLessEqual(high_zoom_terminator, 1.0)
        self.assertGreater(high_zoom_terminator, high_zoom_global)

    def test_tiled_normal_fades_in_after_albedo_tile_activation_threshold(self) -> None:

        activation = MoonTileSettings().activation_diameter_px
        at_activation = MoonRendererGL.tiled_normal_fade_factor(activation, activation)
        threshold_band = MoonRendererGL.tiled_normal_fade_factor(activation + 80.0, activation)
        later_zoom = MoonRendererGL.tiled_normal_fade_factor(activation + 360.0, activation)
        full_detail = MoonRendererGL.tiled_normal_fade_factor(activation + 700.0, activation)

        self.assertEqual(at_activation, 0.0)
        self.assertEqual(threshold_band, 0.0)
        self.assertGreater(later_zoom, 0.0)
        self.assertEqual(full_detail, 1.0)

    def test_tiled_normal_slope_clamp_bounds_aggressive_relief(self) -> None:

        resolved = MoonRendererGL.clamped_tiled_normal_slope((0.9, 0.2, 0.1), 0.25)
        slope = math.sqrt(resolved[0] * resolved[0] + resolved[1] * resolved[1]) / max(resolved[2], 1.0e-9)

        self.assertLessEqual(slope, 0.25 + 1.0e-6)
        self.assertAlmostEqual(sum(component * component for component in resolved), 1.0, delta=1.0e-6)

    def test_terminator_normal_factor_softens_low_light_relief(self) -> None:

        dark_edge = MoonRendererGL.terminator_normal_factor(0.0)
        bright_side = MoonRendererGL.terminator_normal_factor(0.4)

        self.assertGreaterEqual(dark_edge, DEFAULT_MOON_VISUAL_SETTINGS.terminator_normal_min_factor)
        self.assertLess(dark_edge, bright_side)
        self.assertEqual(bright_side, 1.0)

    def test_polar_uv_factors_are_finite_and_activate_only_near_poles(self) -> None:

        for vector in ((0.0, 1.0, 0.0), (0.0, -1.0, 0.0), (1.0e-9, 1.0, 1.0e-9), (-1.0e-9, -1.0, 1.0e-9)):
            texture_u, texture_v = MoonTiledTextureManager.moon_normal_to_uv(vector)
            self.assertTrue(math.isfinite(texture_u))
            self.assertTrue(math.isfinite(texture_v))
            self.assertGreaterEqual(texture_u, 0.0)
            self.assertLessEqual(texture_u, 1.0)
            self.assertGreaterEqual(texture_v, 0.0)
            self.assertLessEqual(texture_v, 1.0)

        self.assertEqual(MoonRendererGL.polar_normal_fade_factor_for_uv(0.5), 0.0)
        self.assertEqual(MoonRendererGL.polar_normal_fade_factor_for_uv(0.0), 1.0)
        self.assertEqual(MoonRendererGL.polar_normal_fade_factor_for_uv(1.0), 1.0)
        self.assertEqual(MoonRendererGL.polar_albedo_fade_factor_for_uv(0.5), 0.0)
        self.assertTrue(MoonRendererGL.polar_analytic_normal_active_for_uv(0.0))
        self.assertTrue(MoonRendererGL.polar_analytic_normal_active_for_uv(1.0))

    def test_seam_proximity_preserves_wrapped_longitude_diagnostics(self) -> None:

        self.assertEqual(MoonRendererGL.seam_proximity_factor(0.0), 1.0)
        self.assertEqual(MoonRendererGL.seam_proximity_factor(1.0), 1.0)
        self.assertEqual(MoonRendererGL.seam_proximity_factor(0.5), 0.0)

    def test_polar_cap_uv_and_blend_are_finite_at_pole_and_boundary(self) -> None:

        self.assertEqual(MoonRendererGL.polar_cap_uv_for_normal((0.0, -1.0, 0.0)), (0.5, 0.5))
        boundary_latitude = DEFAULT_MOON_VISUAL_SETTINGS.polar_cap_blend_start_lat_deg
        boundary = (0.0, -math.sin(math.radians(boundary_latitude)), math.cos(math.radians(boundary_latitude)))
        cap_uv = MoonRendererGL.polar_cap_uv_for_normal(boundary)
        self.assertTrue(all(math.isfinite(value) for value in cap_uv))
        self.assertTrue(0.0 <= cap_uv[0] <= 1.0)
        self.assertTrue(0.0 <= cap_uv[1] <= 1.0)
        self.assertEqual(MoonRendererGL.polar_cap_blend_factor_for_latitude(50.0), 0.0)
        self.assertEqual(MoonRendererGL.polar_cap_blend_factor_for_latitude(-90.0), 1.0)

    def test_polar_cap_blend_is_smooth_monotonic_and_normal_join_is_delayed(self) -> None:

        latitudes = tuple(float(value) for value in range(55, 91))
        albedo_blend = [MoonRendererGL.polar_cap_blend_factor_for_latitude(value) for value in latitudes]
        normal_blend = [MoonRendererGL.polar_cap_normal_blend_factor_for_latitude(value) for value in latitudes]

        self.assertEqual(albedo_blend, sorted(albedo_blend))
        self.assertEqual(normal_blend, sorted(normal_blend))
        self.assertEqual(MoonRendererGL.polar_cap_blend_factor_for_latitude(60.0), 0.0)
        self.assertEqual(MoonRendererGL.polar_cap_blend_factor_for_latitude(78.0), 1.0)
        self.assertEqual(MoonRendererGL.polar_cap_normal_blend_factor_for_latitude(77.0), 0.0)
        self.assertEqual(MoonRendererGL.polar_cap_normal_blend_factor_for_latitude(86.0), 1.0)
        self.assertLess(MoonRendererGL.polar_cap_blend_factor_for_latitude(62.0), 0.02)

    def test_moon_tile_diagnostics_render_path_state_reports_global_and_tiled_paths(self) -> None:

        diagnostics = MoonTileDiagnostics.inactive().with_render_path_state(
            global_albedo_active=True,
            global_normal_active=True,
            tile_bindings=(MoonTileShaderBinding(_texture_tile("albedo", 1, 2, 1), _texture_tile("normal", 1, 2, 1)),),
        )

        self.assertTrue(diagnostics.global_albedo_active)
        self.assertTrue(diagnostics.global_normal_active)
        self.assertTrue(diagnostics.tiled_albedo_active)
        self.assertTrue(diagnostics.tiled_normal_active)
        self.assertEqual(diagnostics.bound_tile_count, 1)
        self.assertEqual(diagnostics.bound_normal_tile_count, 1)

    def test_moon_renderer_shader_reconstructs_lit_sphere_impostor(self) -> None:

        shader = MoonRendererGL._FRAGMENT_SHADER_SOURCE

        self.assertIn("discard", shader)
        self.assertIn("sqrt(max(0.0, 1.0 - r2))", shader)
        self.assertIn("texture2D(u_albedo_texture", shader)
        self.assertIn("dot(normal_moon", shader)
        self.assertIn("smoothstep", shader)

    def test_moon_renderer_evicts_old_textures_but_protects_current_frame_lod(self) -> None:

        renderer = MoonRendererGL()
        old_texture = _FakeTexture()
        current_texture = _FakeTexture()
        renderer._textures[("old",)] = _MoonTextureCacheEntry(old_texture, 8, 8, 512)  # type: ignore[arg-type]
        renderer._textures[("current",)] = _MoonTextureCacheEntry(current_texture, 8, 8, 512)  # type: ignore[arg-type]
        renderer._texture_cache_total_bytes = 1024

        renderer._evict_textures_to_budget(256, protected_cache_keys={("current",)})

        self.assertNotIn(("old",), renderer._textures)
        self.assertIn(("current",), renderer._textures)
        self.assertTrue(old_texture.destroyed)
        self.assertFalse(current_texture.destroyed)

    def test_moon_renderer_protects_current_frame_albedo_and_normal_textures(self) -> None:

        renderer = MoonRendererGL()
        old_texture = _FakeTexture()
        albedo_texture = _FakeTexture()
        normal_texture = _FakeTexture()
        renderer._textures[("old",)] = _MoonTextureCacheEntry(old_texture, 8, 8, 512)  # type: ignore[arg-type]
        renderer._textures[("albedo",)] = _MoonTextureCacheEntry(albedo_texture, 8, 8, 512)  # type: ignore[arg-type]
        renderer._textures[("normal",)] = _MoonTextureCacheEntry(normal_texture, 8, 8, 512)  # type: ignore[arg-type]
        renderer._texture_cache_total_bytes = 1536

        renderer._evict_textures_to_budget(256, protected_cache_keys={("albedo",), ("normal",)})

        self.assertNotIn(("old",), renderer._textures)
        self.assertIn(("albedo",), renderer._textures)
        self.assertIn(("normal",), renderer._textures)
        self.assertTrue(old_texture.destroyed)
        self.assertFalse(albedo_texture.destroyed)
        self.assertFalse(normal_texture.destroyed)

    def test_moon_renderer_releases_old_gpu_tiles_when_manifest_generation_changes(self) -> None:

        renderer = MoonRendererGL()
        old_tile_texture = _FakeTexture()
        old_cap_texture = _FakeTexture()
        global_texture = _FakeTexture()
        renderer._textures[("moon-tile", "1:old", "albedo", 0, 0, 0)] = _MoonTextureCacheEntry(old_tile_texture, 8, 8, 512)  # type: ignore[arg-type]
        renderer._textures[("moon-polar-cap", "1:old", "south")] = _MoonTextureCacheEntry(old_cap_texture, 8, 8, 512)  # type: ignore[arg-type]
        renderer._textures[("moon-albedo-lod", "global")] = _MoonTextureCacheEntry(global_texture, 8, 8, 512)  # type: ignore[arg-type]
        renderer._texture_cache_total_bytes = 1536

        renderer.synchronize_tile_cache_generation("2:new")

        self.assertNotIn(("moon-tile", "1:old", "albedo", 0, 0, 0), renderer._textures)
        self.assertNotIn(("moon-polar-cap", "1:old", "south"), renderer._textures)
        self.assertIn(("moon-albedo-lod", "global"), renderer._textures)
        self.assertTrue(old_tile_texture.destroyed)
        self.assertTrue(old_cap_texture.destroyed)
        self.assertFalse(global_texture.destroyed)

    def test_moon_tiled_mode_inactive_below_threshold(self) -> None:

        with tempfile.TemporaryDirectory() as temp_dir:

            project_root = Path(temp_dir)
            _write_full_moon_tile_level(project_root, 1)
            cache = MoonCache(project_root, executor=_ImmediateExecutor(), tile_settings=MoonTileSettings(activation_diameter_px=900.0, hysteresis_px=100.0, enforce_source_registration=False))  # type: ignore[arg-type]
            diagnostics = cache.prepare_tiled_textures(_fake_draw_state(640.0), _fake_state(datetime(2026, 5, 25, tzinfo=UTC), 30.0))

        self.assertFalse(diagnostics.tiled_mode_active)
        self.assertEqual(diagnostics.fallback_reason, "below_tiled_threshold")

    def test_moon_tiled_mode_active_above_threshold(self) -> None:

        with tempfile.TemporaryDirectory() as temp_dir:

            project_root = Path(temp_dir)
            _write_full_moon_tile_level(project_root, 1)
            cache = MoonCache(project_root, executor=_ImmediateExecutor(), tile_settings=MoonTileSettings(activation_diameter_px=900.0, hysteresis_px=100.0, enforce_source_registration=False))  # type: ignore[arg-type]
            diagnostics = cache.prepare_tiled_textures(_fake_draw_state(1200.0), _fake_state(datetime(2026, 5, 25, tzinfo=UTC), 30.0))

        self.assertTrue(diagnostics.tiled_mode_active)
        self.assertGreater(diagnostics.requested_tile_count, 0)
        self.assertEqual(diagnostics.missing_tile_count, 0)
        self.assertGreater(diagnostics.resident_tile_count, 0)

    def test_unregistered_tiles_are_rejected_by_default(self) -> None:

        with tempfile.TemporaryDirectory() as temp_dir:
            project_root = Path(temp_dir)
            global_source_path = project_root / "global_moon.png"
            _write_global_moon_fixture(global_source_path)
            _write_full_moon_tile_level(project_root, 1, albedo_color=QColor(120, 110, 100))
            _write_registered_tile_manifest(project_root, global_source_path, registered=False)
            cache = MoonCache(
                project_root,
                color_texture_path=global_source_path,
                executor=_ImmediateExecutor(),  # type: ignore[arg-type]
                tile_settings=MoonTileSettings(activation_diameter_px=900.0),
            )
            diagnostics = cache.prepare_tiled_textures(_fake_draw_state(1200.0), _fake_state(datetime(2026, 5, 25, tzinfo=UTC), 30.0))

        self.assertFalse(diagnostics.tiled_mode_active)
        self.assertEqual(diagnostics.fallback_reason, "unregistered_tiles_global_fallback")
        self.assertTrue(diagnostics.tiles_rejected_due_to_registration_mismatch)
        self.assertEqual(diagnostics.registration_warning, MoonTiledTextureManager.REGISTRATION_WARNING)

    def test_registered_tiles_are_accepted_and_debug_override_is_explicit(self) -> None:

        with tempfile.TemporaryDirectory() as temp_dir:
            project_root = Path(temp_dir)
            global_source_path = project_root / "global_moon.png"
            _write_global_moon_fixture(global_source_path)
            _write_full_moon_tile_level(project_root, 1, albedo_color=QColor(120, 110, 100))
            _write_registered_tile_manifest(project_root, global_source_path, registered=True)
            cache = MoonCache(
                project_root,
                color_texture_path=global_source_path,
                executor=_ImmediateExecutor(),  # type: ignore[arg-type]
                tile_settings=MoonTileSettings(activation_diameter_px=900.0),
            )
            registered_diagnostics = cache.prepare_tiled_textures(_fake_draw_state(1200.0), _fake_state(datetime(2026, 5, 25, tzinfo=UTC), 30.0))

            _write_registered_tile_manifest(project_root, global_source_path, registered=False)
            override_cache = MoonCache(
                project_root,
                color_texture_path=global_source_path,
                executor=_ImmediateExecutor(),  # type: ignore[arg-type]
                tile_settings=MoonTileSettings(activation_diameter_px=900.0, allow_unregistered_tiles_debug=True),
            )
            override_diagnostics = override_cache.prepare_tiled_textures(_fake_draw_state(1200.0), _fake_state(datetime(2026, 5, 25, tzinfo=UTC), 30.0))

        self.assertTrue(registered_diagnostics.tiled_mode_active)
        self.assertEqual(registered_diagnostics.tile_registration_status, "registered")
        self.assertTrue(registered_diagnostics.tile_registered_to_global)
        self.assertTrue(override_diagnostics.tiled_mode_active)
        self.assertEqual(override_diagnostics.tile_registration_status, "debug_override_unregistered")

    def test_registered_tiles_with_incomplete_live_coverage_stay_global(self) -> None:

        with tempfile.TemporaryDirectory() as temp_dir:
            project_root = Path(temp_dir)
            global_source_path = project_root / "global_moon.png"
            _write_global_moon_fixture(global_source_path)
            _write_moon_tile(project_root, "albedo", 1, 2, 1, QColor(120, 110, 100))
            _write_registered_tile_manifest(project_root, global_source_path, registered=True)
            cache = MoonCache(
                project_root,
                color_texture_path=global_source_path,
                executor=_ImmediateExecutor(),  # type: ignore[arg-type]
                tile_settings=MoonTileSettings(activation_diameter_px=900.0),
            )
            diagnostics = cache.prepare_tiled_textures(_fake_draw_state(1200.0), _fake_state(datetime(2026, 5, 25, tzinfo=UTC), 30.0))

        self.assertFalse(diagnostics.tiled_shader_active)
        self.assertTrue(diagnostics.tiles_rejected_due_to_unsafe_coverage)
        self.assertEqual(diagnostics.activation_warning, MoonTiledTextureManager.COVERAGE_WARNING)
        self.assertEqual(diagnostics.visible_coverage_estimate, 0.0)
        self.assertLess(diagnostics.candidate_visible_coverage_estimate, 1.0)

    def test_registered_certificate_does_not_allow_wrong_live_tile_pixels(self) -> None:

        with tempfile.TemporaryDirectory() as temp_dir:
            project_root = Path(temp_dir)
            global_source_path = project_root / "global_moon.png"
            _write_global_moon_fixture(global_source_path)
            _write_full_moon_tile_level(project_root, 1, albedo_color=QColor(245, 20, 20))
            _write_registered_tile_manifest(project_root, global_source_path, registered=True)
            cache = MoonCache(
                project_root,
                color_texture_path=global_source_path,
                executor=_ImmediateExecutor(),  # type: ignore[arg-type]
                tile_settings=MoonTileSettings(activation_diameter_px=900.0),
            )
            diagnostics = cache.prepare_tiled_textures(_fake_draw_state(1200.0), _fake_state(datetime(2026, 5, 25, tzinfo=UTC), 30.0))

        self.assertFalse(diagnostics.tiled_shader_active)
        self.assertTrue(diagnostics.tiles_rejected_due_to_live_mismatch)
        self.assertEqual(diagnostics.live_equivalence_status, "fail")
        self.assertGreater(float(diagnostics.live_probe_max_color_delta or 0.0), 0.1)
        self.assertEqual(diagnostics.activation_warning, MoonTiledTextureManager.LIVE_EQUIVALENCE_WARNING)

    def test_live_equivalence_uses_active_global_lod_image(self) -> None:

        with tempfile.TemporaryDirectory() as temp_dir:
            project_root = Path(temp_dir)
            global_source_path = project_root / "global_moon.png"
            _write_global_moon_fixture(global_source_path)
            _write_full_moon_tile_level(project_root, 1, albedo_color=QColor(120, 110, 100))
            _write_registered_tile_manifest(project_root, global_source_path, registered=True)
            active_global_image = QImage(32, 16, QImage.Format.Format_RGB888)
            active_global_image.fill(QColor(10, 240, 30))
            active_global_lod = MoonTextureLod("active-global", 32, 16, active_global_image, ("active-global",))
            cache = MoonCache(
                project_root,
                color_texture_path=global_source_path,
                executor=_ImmediateExecutor(),  # type: ignore[arg-type]
                tile_settings=MoonTileSettings(activation_diameter_px=900.0),
            )
            diagnostics = cache.prepare_tiled_textures(
                _fake_draw_state(1200.0),
                _fake_state(datetime(2026, 5, 25, tzinfo=UTC), 30.0),
                global_albedo_lod=active_global_lod,
            )

        self.assertFalse(diagnostics.tiled_shader_active)
        self.assertTrue(diagnostics.tiles_rejected_due_to_live_mismatch)
        self.assertEqual(diagnostics.live_equivalence_status, "fail")

    def test_manifest_change_invalidates_resident_tile_generation(self) -> None:

        with tempfile.TemporaryDirectory() as temp_dir:
            project_root = Path(temp_dir)
            global_source_path = project_root / "global_moon.png"
            _write_global_moon_fixture(global_source_path)
            _write_full_moon_tile_level(project_root, 1, albedo_color=QColor(120, 110, 100))
            _write_registered_tile_manifest(project_root, global_source_path, registered=True)
            cache = MoonCache(
                project_root,
                color_texture_path=global_source_path,
                executor=_ImmediateExecutor(),  # type: ignore[arg-type]
                tile_settings=MoonTileSettings(
                    activation_diameter_px=900.0,
                    live_asset_watch_enabled=True,
                    live_asset_watch_interval_seconds=0.0,
                ),
            )
            first = cache.prepare_tiled_textures(_fake_draw_state(1200.0), _fake_state(datetime(2026, 5, 25, tzinfo=UTC), 30.0))
            manifest_path = project_root / "assets" / "moon_tiles" / "build_manifest.json"
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
            manifest["runtime_test_revision"] = 2
            manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
            second = cache.prepare_tiled_textures(_fake_draw_state(1200.0), _fake_state(datetime(2026, 5, 25, tzinfo=UTC), 30.0))

        self.assertTrue(first.tiled_shader_active)
        self.assertTrue(second.tiled_shader_active)
        self.assertNotEqual(first.tile_cache_generation_id, second.tile_cache_generation_id)
        self.assertTrue(second.cache_invalidated_this_frame)
        self.assertTrue(second.cache_generation_matches_manifest)
        self.assertFalse(second.bound_tile_texture_predates_current_manifest)

    def test_tiled_albedo_kill_switch_prefers_global_path(self) -> None:

        with tempfile.TemporaryDirectory() as temp_dir:
            project_root = Path(temp_dir)
            _write_full_moon_tile_level(project_root, 1)
            cache = MoonCache(
                project_root,
                executor=_ImmediateExecutor(),  # type: ignore[arg-type]
                tile_settings=MoonTileSettings(
                    activation_diameter_px=900.0,
                    enforce_source_registration=False,
                    disable_tiled_albedo=True,
                ),
            )
            diagnostics = cache.prepare_tiled_textures(_fake_draw_state(1200.0), _fake_state(datetime(2026, 5, 25, tzinfo=UTC), 30.0))

        self.assertFalse(diagnostics.tiled_mode_active)
        self.assertEqual(diagnostics.fallback_reason, "disable_tiled_albedo")

    def test_moon_tiled_mode_hysteresis_avoids_threshold_chatter(self) -> None:

        with tempfile.TemporaryDirectory() as temp_dir:

            project_root = Path(temp_dir)
            _write_full_moon_tile_level(project_root, 1)
            cache = MoonCache(project_root, executor=_ImmediateExecutor(), tile_settings=MoonTileSettings(activation_diameter_px=900.0, hysteresis_px=120.0, enforce_source_registration=False))  # type: ignore[arg-type]
            moon_state = _fake_state(datetime(2026, 5, 25, tzinfo=UTC), 30.0)
            first = cache.prepare_tiled_textures(_fake_draw_state(940.0), moon_state)
            second = cache.prepare_tiled_textures(_fake_draw_state(830.0), moon_state)
            third = cache.prepare_tiled_textures(_fake_draw_state(740.0), moon_state)

        self.assertTrue(first.tiled_mode_active)
        self.assertTrue(second.tiled_mode_active)
        self.assertFalse(third.tiled_mode_active)

    def test_moon_tiled_missing_directory_falls_back_to_global_texture(self) -> None:

        with tempfile.TemporaryDirectory() as temp_dir:

            cache = MoonCache(Path(temp_dir), executor=_ImmediateExecutor())  # type: ignore[arg-type]
            diagnostics = cache.prepare_tiled_textures(_fake_draw_state(1400.0), _fake_state(datetime(2026, 5, 25, tzinfo=UTC), 30.0))

        self.assertFalse(diagnostics.tiled_mode_active)
        self.assertEqual(diagnostics.fallback_reason, "moon_tile_directory_unavailable")
        self.assertEqual(diagnostics.requested_tile_count, 0)

    def test_moon_tiled_delayed_readiness_uses_global_fallback(self) -> None:

        with tempfile.TemporaryDirectory() as temp_dir:

            project_root = Path(temp_dir)
            _write_full_moon_tile_level(project_root, 1)
            executor = _TileDeferredExecutor()
            cache = MoonCache(project_root, executor=executor, tile_settings=MoonTileSettings(activation_diameter_px=900.0, enforce_source_registration=False))  # type: ignore[arg-type]
            diagnostics = cache.prepare_tiled_textures(_fake_draw_state(1200.0), _fake_state(datetime(2026, 5, 25, tzinfo=UTC), 30.0))

        self.assertTrue(diagnostics.tiled_mode_active)
        self.assertGreater(diagnostics.pending_tile_count, 0)
        self.assertGreater(diagnostics.missing_tile_count, 0)
        self.assertEqual(diagnostics.fallback_reason, "tiles_pending_global_fallback")

    def test_moon_tiled_metadata_pending_uses_global_fallback(self) -> None:

        with tempfile.TemporaryDirectory() as temp_dir:

            project_root = Path(temp_dir)
            _write_full_moon_tile_level(project_root, 1)
            cache = MoonCache(project_root, executor=_DeferredExecutor(), tile_settings=MoonTileSettings(activation_diameter_px=900.0, enforce_source_registration=False))  # type: ignore[arg-type]
            diagnostics = cache.prepare_tiled_textures(_fake_draw_state(1200.0), _fake_state(datetime(2026, 5, 25, tzinfo=UTC), 30.0))

        self.assertFalse(diagnostics.tiled_mode_active)
        self.assertEqual(diagnostics.fallback_reason, "moon_tile_index_pending")
        self.assertEqual(diagnostics.pending_tile_count, 0)

    def test_moon_prepare_does_not_run_diagnostics_on_render_path(self) -> None:

        with tempfile.TemporaryDirectory() as temp_dir:

            project_root = Path(temp_dir)
            _write_full_moon_tile_level(project_root, 1, include_normal=True)
            executor = _DiagnosticsDeferredExecutor()
            cache = MoonCache(
                project_root,
                executor=executor,  # type: ignore[arg-type]
                tile_settings=MoonTileSettings(activation_diameter_px=900.0, enforce_source_registration=False),
            )
            diagnostics = cache.prepare_tiled_textures(
                replace(_fake_draw_state(1200.0), tiled_normal_fade_factor=1.0),
                _fake_state(datetime(2026, 5, 25, tzinfo=UTC), 30.0),
                normal_maps_active=True,
            )

        self.assertTrue(diagnostics.tiled_shader_active)
        self.assertEqual(diagnostics.live_equivalence_status, "pending")
        self.assertEqual(diagnostics.normal_tile_statistics, ())
        self.assertIn("_compute_live_equivalence_report", executor.deferred_names)
        self.assertIn("_compute_normal_tile_diagnostics", executor.deferred_names)

    def test_moon_cache_generation_refresh_is_not_per_frame_by_default(self) -> None:

        with tempfile.TemporaryDirectory() as temp_dir:

            project_root = Path(temp_dir)
            _write_full_moon_tile_level(project_root, 1)
            cache = MoonCache(
                project_root,
                executor=_ImmediateExecutor(),  # type: ignore[arg-type]
                tile_settings=MoonTileSettings(activation_diameter_px=900.0, enforce_source_registration=False),
            )
            first = cache.prepare_tiled_textures(_fake_draw_state(1200.0), _fake_state(datetime(2026, 5, 25, tzinfo=UTC), 30.0))
            with patch.object(cache.tile_manager, "_build_generation_snapshot", side_effect=AssertionError("unexpected refresh")):
                second = cache.prepare_tiled_textures(_fake_draw_state(1200.0), _fake_state(datetime(2026, 5, 25, tzinfo=UTC), 30.0))

        self.assertTrue(first.tiled_shader_active)
        self.assertTrue(second.tiled_shader_active)
        self.assertEqual(first.tile_cache_generation_id, second.tile_cache_generation_id)

    def test_moon_prepare_budget_guard_returns_global_fallback(self) -> None:

        with tempfile.TemporaryDirectory() as temp_dir:

            project_root = Path(temp_dir)
            _write_full_moon_tile_level(project_root, 1)
            cache = MoonCache(
                project_root,
                executor=_ImmediateExecutor(),  # type: ignore[arg-type]
                tile_settings=MoonTileSettings(activation_diameter_px=900.0, enforce_source_registration=False),
            )
            perf_calls = {"count": -1}

            def fake_perf_counter() -> float:
                perf_calls["count"] += 1
                return 0.0 if perf_calls["count"] == 0 else 10.0 + float(perf_calls["count"])

            with patch("photometry_app.ui.moon_system.perf_counter", side_effect=fake_perf_counter):
                diagnostics = cache.prepare_tiled_textures(_fake_draw_state(1200.0), _fake_state(datetime(2026, 5, 25, tzinfo=UTC), 30.0))

        self.assertFalse(diagnostics.tiled_mode_active)
        self.assertEqual(diagnostics.fallback_reason, "render_path_budget_global_fallback")
        self.assertFalse(diagnostics.tiled_shader_active)

    def test_moon_tiled_current_frame_tiles_are_protected_from_eviction(self) -> None:

        with tempfile.TemporaryDirectory() as temp_dir:

            project_root = Path(temp_dir)
            _write_full_moon_tile_level(project_root, 1)
            cache = MoonCache(project_root, executor=_ImmediateExecutor(), tile_settings=MoonTileSettings(activation_diameter_px=900.0, cache_budget_bytes=128, enforce_source_registration=False))  # type: ignore[arg-type]
            diagnostics = cache.prepare_tiled_textures(_fake_draw_state(1200.0), _fake_state(datetime(2026, 5, 25, tzinfo=UTC), 30.0))

            resident_keys = tuple(cache.tile_manager._resident_tiles.keys())

        self.assertGreater(diagnostics.tile_cache_memory_estimate, 128)
        self.assertTrue(resident_keys)
        self.assertTrue(all(MoonTiledTextureManager._format_tile_key(key) in diagnostics.selected_tile_keys for key in resident_keys))

    def test_moon_tiled_selection_is_deterministic_for_fixed_state(self) -> None:

        manager = MoonTiledTextureManager(settings=MoonTileSettings(max_visible_tiles=10, selection_grid_samples=7))
        moon_state = _fake_state(datetime(2026, 5, 25, tzinfo=UTC), 30.0)
        first = manager.select_visible_tile_keys(_fake_draw_state(1400.0), moon_state, 2, max_tiles=10, prioritize_terminator=True)
        second = manager.select_visible_tile_keys(_fake_draw_state(1400.0), moon_state, 2, max_tiles=10, prioritize_terminator=True)
        manager.shutdown()

        self.assertEqual(first, second)
        self.assertTrue(first)
        self.assertEqual(first[0], MoonTileKey("albedo", 2, 4, 2))

    def test_moon_tiled_bindings_prefer_broad_coverage_when_sampler_limited(self) -> None:

        with tempfile.TemporaryDirectory() as temp_dir:

            project_root = Path(temp_dir)
            for lod_level in range(4):
                _write_full_moon_tile_level(project_root, lod_level, include_normal=True)
            cache = MoonCache(
                project_root,
                executor=_ImmediateExecutor(),  # type: ignore[arg-type]
                tile_settings=MoonTileSettings(activation_diameter_px=600.0, shader_sampler_limit=6, max_visible_tiles=24, selection_grid_samples=11, enforce_source_registration=False),
            )
            diagnostics = cache.prepare_tiled_textures(_fake_draw_state(3600.0), _fake_state(datetime(2026, 5, 25, tzinfo=UTC), 30.0), normal_maps_active=True)

        self.assertGreater(diagnostics.visible_tile_count, diagnostics.bound_tile_count)
        self.assertLessEqual(diagnostics.bound_tile_count, diagnostics.max_tile_samplers)
        self.assertGreaterEqual(diagnostics.visible_coverage_estimate, 0.85)
        self.assertLessEqual(diagnostics.fallback_coverage_estimate, 0.15)
        self.assertTrue(all("L3" not in key for key in diagnostics.selected_bound_tile_keys))

    def test_moon_tiled_binding_selection_stays_stable_across_zoom_steps(self) -> None:

        with tempfile.TemporaryDirectory() as temp_dir:

            project_root = Path(temp_dir)
            for lod_level in range(4):
                _write_full_moon_tile_level(project_root, lod_level, include_normal=True)
            cache = MoonCache(
                project_root,
                executor=_ImmediateExecutor(),  # type: ignore[arg-type]
                tile_settings=MoonTileSettings(activation_diameter_px=600.0, shader_sampler_limit=6, max_visible_tiles=24, selection_grid_samples=11, enforce_source_registration=False),
            )
            moon_state = _fake_state(datetime(2026, 5, 25, tzinfo=UTC), 30.0)
            reports = [
                cache.prepare_tiled_textures(_fake_draw_state(diameter_px), moon_state, normal_maps_active=True)
                for diameter_px in (950.0, 1300.0, 1900.0, 2800.0, 3600.0)
            ]

        self.assertTrue(all(report.visible_coverage_estimate >= 0.80 for report in reports))
        for previous, current in zip(reports, reports[1:]):
            replaced = set(previous.selected_bound_tile_keys).symmetric_difference(set(current.selected_bound_tile_keys))
            self.assertLessEqual(len(replaced), 8)

    def test_moon_tiled_normal_disable_keeps_albedo_with_global_normal_fallback(self) -> None:

        with tempfile.TemporaryDirectory() as temp_dir:

            project_root = Path(temp_dir)
            _write_full_moon_tile_level(project_root, 1, include_normal=True)
            cache = MoonCache(project_root, executor=_ImmediateExecutor(), tile_settings=MoonTileSettings(activation_diameter_px=900.0, enforce_source_registration=False, disable_tiled_normals=True))  # type: ignore[arg-type]
            diagnostics = cache.prepare_tiled_textures(_fake_draw_state(1200.0), _fake_state(datetime(2026, 5, 25, tzinfo=UTC), 30.0), normal_maps_active=True)
            bindings = cache.moon_tile_shader_bindings(normal_maps_active=True)
            diagnostics = diagnostics.with_render_path_state(global_albedo_active=True, global_normal_active=True, tile_bindings=bindings)
            diagnostics = replace(diagnostics, tiled_normal_disabled_fallback=True)

        self.assertTrue(diagnostics.tiled_albedo_active)
        self.assertTrue(diagnostics.global_normal_active)
        self.assertFalse(diagnostics.tiled_normal_active)
        self.assertEqual(diagnostics.bound_normal_tile_count, 0)
        self.assertTrue(diagnostics.tiled_normal_disabled_fallback)

    def test_neutral_normal_tile_statistics_remain_bounded(self) -> None:

        with tempfile.TemporaryDirectory() as temp_dir:

            project_root = Path(temp_dir)
            _write_full_moon_tile_level(project_root, 1, include_normal=True)
            cache = MoonCache(project_root, executor=_ImmediateExecutor(), tile_settings=MoonTileSettings(activation_diameter_px=900.0, enforce_source_registration=False))  # type: ignore[arg-type]
            diagnostics = cache.prepare_tiled_textures(
                replace(_fake_draw_state(1200.0), tiled_normal_fade_factor=0.5),
                _fake_state(datetime(2026, 5, 25, tzinfo=UTC), 30.0),
                normal_maps_active=True,
            )

        self.assertTrue(diagnostics.normal_tile_statistics)
        self.assertLess(float(diagnostics.max_normal_slope or 1.0), 0.02)
        self.assertFalse(diagnostics.normal_artifact_risk)
        self.assertTrue(all(int(item["outlier_count"]) == 0 for item in diagnostics.normal_tile_statistics))

    def test_moon_height_normal_generation_flat_source_is_neutral(self) -> None:

        height = QImage(32, 16, QImage.Format.Format_Grayscale8)
        height.fill(QColor(128, 128, 128))

        normal = MoonCache._normal_image_from_height_image(height)

        for y in (0, normal.height() // 2, normal.height() - 1):
            for x in (0, normal.width() // 2, normal.width() - 1):
                color = normal.pixelColor(x, y)
                self.assertAlmostEqual(color.red(), 128, delta=1)
                self.assertAlmostEqual(color.green(), 128, delta=1)
                self.assertAlmostEqual(color.blue(), 255, delta=1)

    def test_moon_height_normal_generation_known_bump_orientation(self) -> None:

        width = 64
        height = 32
        image = QImage(width, height, QImage.Format.Format_Grayscale8)
        center_x = width * 0.5
        center_y = height * 0.5
        for y in range(height):
            for x in range(width):
                radial = ((x - center_x) / 8.0) ** 2 + ((y - center_y) / 5.0) ** 2
                value = int(round(96.0 + 128.0 * math.exp(-0.5 * radial)))
                image.setPixelColor(x, y, QColor(value, value, value))

        normal = MoonCache._normal_image_from_height_image(image)
        left = normal.pixelColor(width // 2 - 6, height // 2)
        right = normal.pixelColor(width // 2 + 6, height // 2)
        top = normal.pixelColor(width // 2, height // 2 - 4)
        bottom = normal.pixelColor(width // 2, height // 2 + 4)

        self.assertLess(left.red(), 128)
        self.assertGreater(right.red(), 128)
        self.assertLess(top.green(), 128)
        self.assertGreater(bottom.green(), 128)

    def test_polar_height_normal_generation_suppresses_exact_pole_derivatives(self) -> None:

        image = QImage(32, 16, QImage.Format.Format_Grayscale8)
        for y in range(image.height()):
            for x in range(image.width()):
                value = 20 if x % 2 == 0 else 235
                image.setPixelColor(x, y, QColor(value, value, value))
        normal = MoonCache._normal_image_from_height_image(
            image,
            smoothing_passes=1,
            polar_smoothing_passes=2,
            polar_derivative_fade_start_lat_deg=60.0,
            polar_derivative_fade_end_lat_deg=85.0,
        )

        for y in (0, normal.height() - 1):
            for x in (0, normal.width() // 2, normal.width() - 1):
                color = normal.pixelColor(x, y)
                self.assertAlmostEqual(color.red(), 128, delta=1)
                self.assertAlmostEqual(color.green(), 128, delta=1)
                self.assertAlmostEqual(color.blue(), 255, delta=1)

    def test_moon_tiled_shader_inactive_without_tile_assets(self) -> None:

        with tempfile.TemporaryDirectory() as temp_dir:

            cache = MoonCache(Path(temp_dir), executor=_ImmediateExecutor())  # type: ignore[arg-type]
            diagnostics = cache.prepare_tiled_textures(_fake_draw_state(1400.0), _fake_state(datetime(2026, 5, 25, tzinfo=UTC), 30.0))

        self.assertFalse(diagnostics.tiled_shader_active)
        self.assertEqual(diagnostics.bound_tile_count, 0)
        self.assertEqual(diagnostics.shader_tile_fallback_reason, "tiled_mode_inactive")

    def test_moon_tiled_shader_inactive_when_tiles_not_resident(self) -> None:

        with tempfile.TemporaryDirectory() as temp_dir:

            project_root = Path(temp_dir)
            _write_full_moon_tile_level(project_root, 1)
            cache = MoonCache(project_root, executor=_TileDeferredExecutor(), tile_settings=MoonTileSettings(activation_diameter_px=900.0, enforce_source_registration=False))  # type: ignore[arg-type]
            diagnostics = cache.prepare_tiled_textures(_fake_draw_state(1200.0), _fake_state(datetime(2026, 5, 25, tzinfo=UTC), 30.0))

        self.assertTrue(diagnostics.tiled_mode_active)
        self.assertFalse(diagnostics.tiled_shader_active)
        self.assertEqual(diagnostics.bound_tile_count, 0)
        self.assertEqual(diagnostics.shader_tile_fallback_reason, "no_resident_shader_tiles_global_fallback")

    def test_moon_tiled_shader_active_with_resident_albedo_tile(self) -> None:

        with tempfile.TemporaryDirectory() as temp_dir:

            project_root = Path(temp_dir)
            _write_full_moon_tile_level(project_root, 1)
            cache = MoonCache(project_root, executor=_ImmediateExecutor(), tile_settings=MoonTileSettings(activation_diameter_px=900.0, shader_sampler_limit=4, enforce_source_registration=False))  # type: ignore[arg-type]
            diagnostics = cache.prepare_tiled_textures(_fake_draw_state(1200.0), _fake_state(datetime(2026, 5, 25, tzinfo=UTC), 30.0))

        self.assertTrue(diagnostics.tiled_shader_active)
        self.assertGreater(diagnostics.bound_albedo_tile_count, 0)
        self.assertEqual(diagnostics.bound_normal_tile_count, 0)
        self.assertLessEqual(diagnostics.bound_tile_count, 4)
        self.assertTrue(diagnostics.selected_bound_tile_keys)

    def test_moon_tiled_shader_active_with_resident_albedo_and_normal_tiles(self) -> None:

        with tempfile.TemporaryDirectory() as temp_dir:

            project_root = Path(temp_dir)
            _write_full_moon_tile_level(project_root, 1, include_normal=True)
            cache = MoonCache(project_root, executor=_ImmediateExecutor(), tile_settings=MoonTileSettings(activation_diameter_px=900.0, shader_sampler_limit=4, enforce_source_registration=False))  # type: ignore[arg-type]
            diagnostics = cache.prepare_tiled_textures(_fake_draw_state(1200.0), _fake_state(datetime(2026, 5, 25, tzinfo=UTC), 30.0), normal_maps_active=True)

        self.assertTrue(diagnostics.tiled_shader_active)
        self.assertGreater(diagnostics.bound_normal_tile_count, 0)
        self.assertEqual(diagnostics.bound_normal_tile_count, diagnostics.bound_albedo_tile_count)

    def test_moon_tiled_shader_missing_normal_uses_albedo_with_global_normal_fallback(self) -> None:

        with tempfile.TemporaryDirectory() as temp_dir:

            project_root = Path(temp_dir)
            _write_full_moon_tile_level(project_root, 1, include_normal=False)
            cache = MoonCache(project_root, executor=_ImmediateExecutor(), tile_settings=MoonTileSettings(activation_diameter_px=900.0, shader_sampler_limit=4, enforce_source_registration=False))  # type: ignore[arg-type]
            diagnostics = cache.prepare_tiled_textures(_fake_draw_state(1200.0), _fake_state(datetime(2026, 5, 25, tzinfo=UTC), 30.0), normal_maps_active=True)
            bindings = cache.moon_tile_shader_bindings(normal_maps_active=True)

        self.assertTrue(diagnostics.tiled_shader_active)
        self.assertGreater(diagnostics.bound_albedo_tile_count, 0)
        self.assertEqual(diagnostics.bound_normal_tile_count, 0)
        self.assertTrue(all(binding.normal_tile is None for binding in bindings))

    def test_moon_tiled_shader_uv_inside_tile_routes_to_tile(self) -> None:

        binding = MoonTileShaderBinding(_texture_tile("albedo", 1, 2, 1))
        bounds = binding.uv_bounds
        route = MoonRendererGL.tile_sample_route_for_uv((bounds.u_min + bounds.u_max) * 0.5, (bounds.v_min + bounds.v_max) * 0.5, (binding,))

        self.assertEqual(route.source, "tile")
        self.assertEqual(route.tile_key, "albedo:L1/2_1")
        self.assertAlmostEqual(route.local_u, 0.5)
        self.assertAlmostEqual(route.local_v, 0.5)
        self.assertGreater(route.edge_weight, 0.99)

    def test_moon_tiled_shader_uv_outside_tile_routes_to_global(self) -> None:

        binding = MoonTileShaderBinding(_texture_tile("albedo", 1, 0, 0))
        route = MoonRendererGL.tile_sample_route_for_uv(0.99, 0.99, (binding,))

        self.assertEqual(route.source, "global")
        self.assertEqual(route.tile_key, "")

    def test_moon_tiled_shader_uv_wrap_is_stable_near_global_seam(self) -> None:

        image = QImage(8, 8, QImage.Format.Format_RGB888)
        image.fill(QColor(120, 130, 140))
        seam_tile = MoonTextureTile(
            key=MoonTileKey("albedo", 1, 0, 0),
            label="albedo:L1/seam",
            path=Path("/tmp/albedo_seam.png"),
            uv_bounds=MoonTileUvBounds(0.95, 0.2, 0.05, 0.8),
            image=image,
            cache_key=("tile", "albedo", "seam"),
        )
        binding = MoonTileShaderBinding(seam_tile)

        upper_route = MoonRendererGL.tile_sample_route_for_uv(0.99, 0.5, (binding,))
        lower_route = MoonRendererGL.tile_sample_route_for_uv(0.01, 0.5, (binding,))

        self.assertEqual(upper_route.source, "tile")
        self.assertEqual(lower_route.source, "tile")
        self.assertAlmostEqual(upper_route.local_u + lower_route.local_u, 1.0, delta=1.0e-6)
        self.assertTrue(0.0 <= upper_route.local_u <= 1.0)
        self.assertTrue(0.0 <= lower_route.local_u <= 1.0)

    def test_visible_poles_enable_singularity_guard_without_out_of_bounds_sampling(self) -> None:

        with tempfile.TemporaryDirectory() as temp_dir:

            project_root = Path(temp_dir)
            _write_full_moon_tile_level(project_root, 0, include_normal=True)
            cache = MoonCache(project_root, executor=_ImmediateExecutor(), tile_settings=MoonTileSettings(activation_diameter_px=900.0, enforce_source_registration=False))  # type: ignore[arg-type]
            diagnostics = cache.prepare_tiled_textures(
                replace(_fake_draw_state(1200.0), tiled_normal_fade_factor=1.0),
                _fake_state(datetime(2026, 5, 25, tzinfo=UTC), 30.0),
                normal_maps_active=True,
            )

        self.assertTrue(diagnostics.visible_uv_near_north_pole)
        self.assertTrue(diagnostics.visible_uv_near_south_pole)
        self.assertEqual(diagnostics.visible_pole_proximity, 1.0)
        self.assertEqual(diagnostics.polar_normal_fade_factor, 1.0)
        self.assertTrue(diagnostics.polar_analytic_normal_active)
        self.assertTrue(diagnostics.pole_singularity_guard_active)
        self.assertFalse(diagnostics.any_out_of_bounds_tile_sampling)

    def test_moon_tiled_shader_bound_selection_is_deterministic_and_limited(self) -> None:

        with tempfile.TemporaryDirectory() as temp_dir:

            project_root = Path(temp_dir)
            _write_full_moon_tile_level(project_root, 2, include_normal=True)
            cache = MoonCache(project_root, executor=_ImmediateExecutor(), tile_settings=MoonTileSettings(activation_diameter_px=900.0, shader_sampler_limit=2, max_visible_tiles=12, enforce_source_registration=False))  # type: ignore[arg-type]
            moon_state = _fake_state(datetime(2026, 5, 25, tzinfo=UTC), 30.0)
            diagnostics = cache.prepare_tiled_textures(_fake_draw_state(1400.0), moon_state, normal_maps_active=True)
            first = cache.moon_tile_shader_bindings(normal_maps_active=True, sampler_limit=2)
            second = cache.moon_tile_shader_bindings(normal_maps_active=True, sampler_limit=2)

        self.assertEqual(first, second)
        self.assertEqual(len(first), 2)
        self.assertEqual(diagnostics.tile_sampler_limit, 2)
        self.assertLessEqual(diagnostics.bound_tile_count, 2)

    def test_moon_renderer_tile_uniforms_set_bounds_and_sampler_limit(self) -> None:

        renderer = MoonRendererGL()
        renderer._uniform_locations = {name: index for index, name in enumerate(_moon_renderer_uniform_names())}
        functions = _FakeUniformFunctions(renderer._uniform_locations)
        albedo = _texture_tile("albedo", 1, 2, 1)
        normal = _texture_tile("normal", 1, 2, 1)
        binding = MoonTileShaderBinding(albedo, normal)

        renderer._set_tile_uniform_values(functions, (binding,), tile_edge_fade_fraction=0.04)  # type: ignore[arg-type]

        self.assertEqual(functions.values["u_moonTileAlbedoCount"], 1)
        self.assertEqual(functions.values["u_moonTileAlbedo0"], 2)
        self.assertEqual(functions.values["u_moonTileNormal0"], 8)
        self.assertEqual(functions.values["u_moonTileNormalEnabled0"], 1)
        self.assertEqual(functions.values["u_moonTileBounds0"], (0.5, 0.5, 0.75, 1.0))
        self.assertAlmostEqual(cast(float, functions.values["u_moonTileEdgeFade"]), 0.04)

    def test_moon_renderer_polar_cap_uniforms_and_diagnostics_use_safe_fallback(self) -> None:

        renderer = MoonRendererGL()
        renderer._uniform_locations = {name: index for index, name in enumerate(_moon_renderer_uniform_names())}
        functions = _FakeUniformFunctions(renderer._uniform_locations)
        cap_image = QImage(16, 16, QImage.Format.Format_RGB888)
        cap_image.fill(QColor(100, 110, 120))
        south_cap = MoonTextureLod("south-cap", 16, 16, cap_image, ("south-cap",))
        binding = MoonPolarCapBinding(
            projection="azimuthal_equidistant",
            latitude_coverage_deg=60.0,
            blend_start_lat_deg=60.0,
            blend_end_lat_deg=78.0,
            texture_size_px=2048,
            normal_blend_start_lat_deg=78.0,
            normal_blend_end_lat_deg=86.0,
            boundary_luma_delta=0.001,
            boundary_color_delta=0.002,
            boundary_contrast_delta=0.015,
            core_sharpness_estimate=0.021,
            core_vs_surrounding_detail_delta=-0.03,
            cap_metrics={
                "south": {
                    "boundary_contrast_delta": 0.015,
                    "core_sharpness_estimate": 0.021,
                    "core_vs_surrounding_detail_delta": -0.03,
                    "footprint_risk": False,
                }
            },
            south_albedo=south_cap,
        )
        draw_state = replace(
            _fake_draw_state(1200.0),
            polar_cap_normal_strength=0.022,
            polar_cap_blend_start_lat_deg=62.0,
            polar_cap_blend_end_lat_deg=80.0,
            polar_cap_blend_curve_power=1.6,
            polar_cap_normal_blend_start_lat_deg=80.5,
            polar_cap_normal_blend_end_lat_deg=87.0,
            polar_cap_albedo_sharpness=0.28,
            polar_cap_local_contrast=1.08,
            polar_cap_boundary_match_strength=0.48,
            polar_cap_core_match_strength=0.75,
        )

        renderer._set_polar_cap_uniform_values(functions, draw_state, binding)  # type: ignore[arg-type]

        self.assertEqual(functions.values["u_moonPolarCapSouthAlbedoEnabled"], 1)
        self.assertEqual(functions.values["u_moonPolarCapNorthAlbedoEnabled"], 0)
        self.assertEqual(functions.values["u_moonPolarCapSouthAlbedo"], 15)
        self.assertEqual(functions.values["u_polarCapNormalStrength"], 0.022)
        self.assertEqual(functions.values["u_polarCapBlendStartLatDeg"], 62.0)
        self.assertEqual(functions.values["u_polarCapBlendEndLatDeg"], 80.0)
        self.assertEqual(functions.values["u_polarCapBlendCurvePower"], 1.6)
        self.assertEqual(functions.values["u_polarCapNormalBlendStartLatDeg"], 80.5)
        self.assertEqual(functions.values["u_polarCapNormalBlendEndLatDeg"], 87.0)
        self.assertEqual(functions.values["u_polarCapTextureSizePx"], 2048.0)
        self.assertEqual(functions.values["u_polarCapAlbedoSharpness"], 0.28)
        self.assertEqual(functions.values["u_polarCapLocalContrast"], 1.08)
        self.assertEqual(functions.values["u_polarCapBoundaryMatchStrength"], 0.48)
        self.assertEqual(functions.values["u_polarCapCoreMatchStrength"], 0.75)
        visible_south = replace(
            MoonTileDiagnostics.inactive(),
            artifact_lunar_latitude_deg=-90.0,
            visible_uv_near_south_pole=True,
        )
        active = visible_south.with_polar_cap_state(binding)
        fallback = visible_south.with_polar_cap_state(None)
        self.assertTrue(active.polar_cap_active)
        self.assertEqual(active.polar_cap_blend_factor, 1.0)
        self.assertEqual(active.polar_cap_projection, "azimuthal_equidistant")
        self.assertEqual(active.polar_cap_size_px, 2048)
        self.assertEqual(active.polar_cap_boundary_color_delta, 0.002)
        self.assertEqual(active.polar_cap_boundary_contrast_delta, 0.015)
        self.assertEqual(active.polar_cap_core_sharpness_estimate, 0.021)
        self.assertEqual(active.polar_cap_core_vs_surrounding_detail_delta, -0.03)
        self.assertFalse(active.polar_cap_ring_risk)
        self.assertFalse(active.polar_cap_footprint_risk)
        self.assertFalse(fallback.polar_cap_active)
        self.assertIn("phase5l_fallback", fallback.polar_cap_fallback_reason)

    def test_polar_cap_binding_respects_disabled_normal_map_path(self) -> None:

        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            global_source = root / "global.png"
            _write_global_moon_fixture(global_source)
            _write_registered_tile_manifest(root, global_source, registered=True)
            cap_root = root / "assets" / "moon_tiles" / "polar_caps"
            _write_global_moon_fixture(cap_root / "albedo" / "south.png")
            _write_global_moon_fixture(cap_root / "normal" / "south.png")
            manifest_path = root / "assets" / "moon_tiles" / "build_manifest.json"
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
            manifest["polar_caps"] = {
                "enabled": True,
                "projection": "azimuthal_equidistant",
                "texture_size": 32,
                "latitude_coverage_deg": 60.0,
                "blend_start_lat_deg": 65.0,
                "blend_end_lat_deg": 75.0,
                "normal_blend_start_lat_deg": 78.0,
                "normal_blend_end_lat_deg": 86.0,
                "validation": {"status": "pass"},
                "caps": {
                    "south": {
                        "albedo_path": "polar_caps/albedo/south.png",
                        "normal_path": "polar_caps/normal/south.png",
                    }
                },
            }
            manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
            cache = MoonCache(root, color_texture_path=global_source, executor=_ImmediateExecutor())  # type: ignore[arg-type]
            try:
                normal_binding = cache.moon_polar_cap_binding(normal_maps_active=True)
                albedo_only_binding = cache.moon_polar_cap_binding(normal_maps_active=False)
            finally:
                cache.shutdown()

        self.assertIsNotNone(normal_binding)
        self.assertIsNotNone(albedo_only_binding)
        assert normal_binding is not None and albedo_only_binding is not None
        self.assertIsNotNone(normal_binding.south_normal)
        self.assertIsNone(albedo_only_binding.south_normal)

    def test_polar_cap_binding_returns_none_while_background_load_pending(self) -> None:

        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            global_source = root / "global.png"
            _write_global_moon_fixture(global_source)
            _write_registered_tile_manifest(root, global_source, registered=True)
            cap_root = root / "assets" / "moon_tiles" / "polar_caps"
            _write_global_moon_fixture(cap_root / "albedo" / "south.png")
            manifest_path = root / "assets" / "moon_tiles" / "build_manifest.json"
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
            manifest["polar_caps"] = {
                "enabled": True,
                "projection": "azimuthal_equidistant",
                "texture_size": 32,
                "validation": {"status": "pass"},
                "caps": {"south": {"albedo_path": "polar_caps/albedo/south.png"}},
            }
            manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
            executor = _DiagnosticsDeferredExecutor()
            cache = MoonCache(root, color_texture_path=global_source, executor=executor)  # type: ignore[arg-type]
            try:
                binding = cache.moon_polar_cap_binding(normal_maps_active=False)
            finally:
                cache.shutdown()

        self.assertIsNone(binding)
        self.assertIn("_compute_polar_cap_binding", executor.deferred_names)

    def test_moon_renderer_protects_bound_tile_cache_keys_for_current_frame(self) -> None:

        global_lod = MoonTextureLod("global", 8, 4, QImage(8, 4, QImage.Format.Format_RGB888), ("global",))
        normal_lod = MoonTextureLod("normal", 8, 4, QImage(8, 4, QImage.Format.Format_RGB888), ("global-normal",))
        albedo_tile = _texture_tile("albedo", 1, 2, 1)
        normal_tile = _texture_tile("normal", 1, 2, 1)
        south_cap = MoonTextureLod("south-cap", 8, 8, QImage(8, 8, QImage.Format.Format_RGB888), ("south-cap",))
        polar_binding = MoonPolarCapBinding("azimuthal_equidistant", 60.0, 65.0, 75.0, south_albedo=south_cap)

        protected = MoonRendererGL.protected_cache_keys_for_draw(global_lod, normal_lod, (MoonTileShaderBinding(albedo_tile, normal_tile),), polar_binding)

        self.assertIn(("global",), protected)
        self.assertIn(("global-normal",), protected)
        self.assertIn(tuple(albedo_tile.cache_key), protected)
        self.assertIn(tuple(normal_tile.cache_key), protected)
        self.assertIn(("south-cap",), protected)

    def test_moon_renderer_shader_declares_fixed_tile_samplers(self) -> None:

        shader = MoonRendererGL._FRAGMENT_SHADER_SOURCE

        self.assertIn("u_moonTileAlbedo0", shader)
        self.assertIn("u_moonTileAlbedo3", shader)
        self.assertIn("u_moonTileNormal0", shader)
        self.assertIn("moonTileContainsUv", shader)
        self.assertIn("sampleMoonAlbedo", shader)
        self.assertIn("polar_route_color", shader)
        self.assertIn("moonPolarCapCoreFactor", shader)
        self.assertIn("abs(resolved_albedo_sample - global_albedo)", shader)
        self.assertIn("sampleMoonPolarCapAlbedo", shader)
        self.assertIn("moonPolarCapUv", shader)
        self.assertIn("moonSmootherstep", shader)
        self.assertIn("moonPolarCapNormalBlendFactor", shader)
        self.assertIn("u_moonPolarCapSouthAlbedo", shader)

    def test_moon_renderer_draw_does_not_take_tile_manager_or_tile_cache(self) -> None:

        signature = inspect.signature(MoonRendererGL.draw)

        self.assertNotIn("tile_manager", signature.parameters)
        self.assertNotIn("tiled_texture_manager", signature.parameters)
        self.assertNotIn("moon_tile_cache", signature.parameters)

    def test_default_visual_smoke_cases_cover_high_zoom_tile_scenarios(self) -> None:

        labels = {case.expected_phase for case in DEFAULT_MOON_VISUAL_SMOKE_CASES}

        self.assertIn("high-zoom-full", labels)
        self.assertIn("high-zoom-first-quarter", labels)
        self.assertIn("high-zoom-crescent", labels)
        self.assertIn("high-zoom-missing-tiles", labels)

    def test_default_visual_smoke_cases_cover_required_moon_scenarios(self) -> None:

        labels = {case.expected_phase for case in DEFAULT_MOON_VISUAL_SMOKE_CASES}

        self.assertIn("new", labels)
        self.assertIn("crescent", labels)
        self.assertIn("first-quarter", labels)
        self.assertIn("full", labels)
        self.assertIn("horizon", labels)
        self.assertIn("detail", labels)
        self.assertIn("scrub", labels)
        self.assertIn("thin-crescent-earthshine", labels)
        self.assertIn("terminator-detail", labels)
        self.assertIn("full-low-terrain", labels)
        self.assertIn("horizon-atmosphere", labels)
        self.assertIn("normal-detail", labels)
        self.assertIn("scrub-no-stall", labels)


if __name__ == "__main__":
    unittest.main()
