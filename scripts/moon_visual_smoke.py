from __future__ import annotations

import argparse
from concurrent.futures import Future
from dataclasses import replace
import json
import math
import os
import tempfile
from datetime import timedelta
from pathlib import Path
from types import SimpleNamespace

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QPoint, QPointF, QRectF, Qt
from PySide6.QtGui import QColor, QImage, QPainter, QWheelEvent
from PySide6.QtWidgets import QApplication

from photometry_app.core.sky_atlas import SkyAtlasObject

from photometry_app.ui.moon_system import (
    DEFAULT_MOON_VISUAL_SETTINGS,
    DEFAULT_MOON_VISUAL_SMOKE_CASES,
    MoonAtmosphereModel,
    MoonCache,
    MoonDrawState,
    MoonEarthshineModel,
    MoonEphemeris,
    MoonRendererGL,
    MoonTileDiagnostics,
    MoonTileSettings,
    compare_lunar_orientation_providers,
    select_lunar_orientation_provider,
)
try:
    from scripts.build_moon_tiles import validate_moon_tile_tree
except ModuleNotFoundError:
    from build_moon_tiles import validate_moon_tile_tree


class _ImmediateExecutor:

    def submit(self, function, *args, **kwargs):  # type: ignore[no-untyped-def]
        future: Future = Future()
        try:
            future.set_result(function(*args, **kwargs))
        except Exception as exc:
            future.set_exception(exc)
        return future


def _write_smoke_tile(root: Path, kind: str, lod_level: int, tile_x: int, tile_y: int) -> None:
    tile_dir = root / "assets" / "moon_tiles" / kind / f"L{lod_level}"
    tile_dir.mkdir(parents=True, exist_ok=True)
    image = QImage(12, 12, QImage.Format.Format_RGB888)
    image.fill(QColor(148 + tile_x * 3, 138 + tile_y * 3, 126) if kind == "albedo" else QColor(128, 128, 255))
    image.save(str(tile_dir / f"{tile_x}_{tile_y}.png"))


def _write_smoke_tile_level(root: Path, lod_level: int, *, include_normal: bool) -> None:
    columns = 2 ** (lod_level + 1)
    rows = 2**lod_level
    for tile_y in range(rows):
        for tile_x in range(columns):
            _write_smoke_tile(root, "albedo", lod_level, tile_x, tile_y)
            if include_normal:
                _write_smoke_tile(root, "normal", lod_level, tile_x, tile_y)


def _finite_vector(vector: tuple[float, float, float]) -> bool:
    return all(math.isfinite(component) for component in vector)


def _finite_matrix(matrix: tuple[tuple[float, float, float], tuple[float, float, float], tuple[float, float, float]]) -> bool:
    return all(_finite_vector(row) for row in matrix)


def _state_is_finite(state: object) -> bool:
    scalar_values = (
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
    )
    return (
        all(math.isfinite(float(value)) for value in scalar_values)
        and _finite_vector(state.sun_dir_moon_local)
        and _finite_vector(state.earth_dir_moon_local)
        and _finite_vector(state.observer_dir_moon_local)
        and _finite_matrix(state.view_to_moon_matrix)
    )


def _orientation_comparison_summary(comparisons: list[dict[str, object]]) -> dict[str, object]:
    if not comparisons:
        return {"case_count": 0}
    return {
        "case_count": len(comparisons),
        "max_body_axis_delta_deg": max(float(item["max_body_axis_delta_deg"]) for item in comparisons),
        "max_libration_longitude_delta_deg": max(float(item["libration_longitude_delta_deg"]) for item in comparisons),
        "max_libration_latitude_delta_deg": max(float(item["libration_latitude_delta_deg"]) for item in comparisons),
        "max_lunar_north_position_angle_delta_deg": max(float(item["lunar_north_position_angle_delta_deg"]) for item in comparisons),
    }


def _tile_route_dict(route: object) -> dict[str, object]:
    return {
        "source": route.source,
        "tile_key": route.tile_key,
        "local_u": route.local_u,
        "local_v": route.local_v,
        "edge_weight": route.edge_weight,
    }


def _diagnostics_with_render_paths(cache: MoonCache, draw_state: MoonDrawState, diagnostics: object, *, normal_maps_active: bool) -> object:
    global_normal_lod = cache.best_available_normal_texture_lod(draw_state.diameter_px) if normal_maps_active else None
    tile_bindings = cache.moon_tile_shader_bindings(normal_maps_active=normal_maps_active)
    polar_cap_binding = cache.moon_polar_cap_binding(normal_maps_active=normal_maps_active)
    return diagnostics.with_render_path_state(
        global_albedo_active=True,
        global_normal_active=global_normal_lod is not None,
        tile_bindings=tile_bindings,
        tiled_normal_strength=draw_state.tiled_normal_strength,
        tiled_normal_fade_factor=draw_state.tiled_normal_fade_factor,
        terminator_normal_factor=draw_state.terminator_normal_factor,
    ).with_polar_cap_state(polar_cap_binding)


def _smoke_moon_draw_state(state: object, field_width_deg: float, *, viewport_px: int = 1024) -> MoonDrawState:
    diameter_px = max(2.0, float(state.angular_diameter_deg) / max(1.0e-6, float(field_width_deg)) * float(viewport_px))
    global_strength, global_terminator_strength = MoonRendererGL.resolved_terrain_normal_strength_components(
        DEFAULT_MOON_VISUAL_SETTINGS.global_normal_strength,
        DEFAULT_MOON_VISUAL_SETTINGS.global_terminator_normal_strength,
        diameter_px,
    )
    tiled_strength, tiled_terminator_strength = MoonRendererGL.resolved_terrain_normal_strength_components(
        DEFAULT_MOON_VISUAL_SETTINGS.tiled_normal_strength,
        DEFAULT_MOON_VISUAL_SETTINGS.tiled_terminator_normal_strength,
        diameter_px,
    )
    return MoonDrawState(
        viewport_width_px=viewport_px,
        viewport_height_px=viewport_px,
        center_x_px=viewport_px * 0.5,
        center_y_px=viewport_px * 0.5,
        radius_px=diameter_px * 0.5,
        opacity=1.0,
        sun_direction_moon_local=state.sun_dir_moon_local,
        view_to_moon_matrix=state.view_to_moon_matrix,
        selected_lod_label="smoke",
        earth_direction_moon_local=state.earth_dir_moon_local,
        terrain_normal_strength=max(global_strength, global_terminator_strength),
        terrain_normal_strength_global=global_strength,
        terrain_normal_strength_terminator=global_terminator_strength,
        tiled_normal_strength=tiled_strength,
        tiled_terminator_normal_strength=tiled_terminator_strength,
        tiled_normal_max_slope=DEFAULT_MOON_VISUAL_SETTINGS.tiled_normal_max_slope,
        tiled_normal_fade_factor=MoonRendererGL.tiled_normal_fade_factor(diameter_px),
        terminator_normal_factor=MoonRendererGL.terminator_normal_factor(0.0),
    )


def _moon_focus_zoom_sequence_report() -> dict[str, object]:
    from photometry_app.ui.main_window import _SkyAtlasViewWidget

    app = QApplication.instance() or QApplication([])
    widget = _SkyAtlasViewWidget()
    try:
        widget.resize(800, 400)
        rect = QRectF(0.0, 0.0, 800.0, 400.0)
        moon = SkyAtlasObject(
            name="Moon",
            object_type="Natural Satellite",
            ra_deg=120.0,
            dec_deg=14.0,
            magnitude=-12.2,
            catalog="Solar System",
            searchable=True,
            label_visible=True,
            selectable=True,
        )
        widget.set_selected_object(moon)
        widget._set_camera_state(moon.ra_deg, moon.dec_deg, 2.0)
        sequence: list[dict[str, float | int]] = []

        def append_step(step_index: int) -> None:
            field_width_deg = float(widget._field_width_deg)
            field_height_deg = float(widget._field_height_deg(rect))
            sequence.append(
                {
                    "step": step_index,
                    "field_width_deg": field_width_deg,
                    "field_height_deg": field_height_deg,
                    "fov_aspect_ratio": field_width_deg / max(1.0e-12, field_height_deg),
                }
            )

        append_step(0)
        for step_index in range(1, 6):
            widget.wheelEvent(
                QWheelEvent(
                    QPointF(400.0, 200.0),
                    QPointF(400.0, 200.0),
                    QPoint(0, 0),
                    QPoint(0, 120),
                    Qt.MouseButton.NoButton,
                    Qt.KeyboardModifier.NoModifier,
                    Qt.ScrollPhase.NoScrollPhase,
                    False,
                )
            )
            append_step(step_index)

        widget.set_selected_object(None)
        widget._moon_focus_zoom_latched = False
        widget._sky_view_moon_last_state = SimpleNamespace(ra_deg=120.0, dec_deg=14.0)
        widget._set_camera_state(120.0, 14.0, 2.0)
        drift_sequence: list[dict[str, float | int]] = []

        def append_drift_step(step_index: int) -> None:
            field_width_deg = float(widget._field_width_deg)
            field_height_deg = float(widget._field_height_deg(rect))
            drift_sequence.append(
                {
                    "step": step_index,
                    "field_width_deg": field_width_deg,
                    "field_height_deg": field_height_deg,
                    "fov_aspect_ratio": field_width_deg / max(1.0e-12, field_height_deg),
                }
            )

        append_drift_step(0)
        widget.wheelEvent(
            QWheelEvent(
                QPointF(400.0, 200.0),
                QPointF(400.0, 200.0),
                QPoint(0, 0),
                QPoint(0, 120),
                Qt.MouseButton.NoButton,
                Qt.KeyboardModifier.NoModifier,
                Qt.ScrollPhase.NoScrollPhase,
                False,
            )
        )
        append_drift_step(1)
        widget._sky_view_moon_last_state = SimpleNamespace(ra_deg=126.0, dec_deg=14.0)
        for step_index in range(2, 6):
            widget.wheelEvent(
                QWheelEvent(
                    QPointF(400.0, 200.0),
                    QPointF(400.0, 200.0),
                    QPoint(0, 0),
                    QPoint(0, 120),
                    Qt.MouseButton.NoButton,
                    Qt.KeyboardModifier.NoModifier,
                    Qt.ScrollPhase.NoScrollPhase,
                    False,
                )
            )
            append_drift_step(step_index)

        widths = [float(item["field_width_deg"]) for item in sequence]
        heights = [float(item["field_height_deg"]) for item in sequence]
        aspect_ratios = [float(item["fov_aspect_ratio"]) for item in sequence]
        drift_widths = [float(item["field_width_deg"]) for item in drift_sequence]
        drift_heights = [float(item["field_height_deg"]) for item in drift_sequence]
        drift_aspect_ratios = [float(item["fov_aspect_ratio"]) for item in drift_sequence]
        expected_aspect_ratio = rect.width() / rect.height()
        finite = all(math.isfinite(value) for pair in zip(widths, heights, aspect_ratios, drift_widths, drift_heights, drift_aspect_ratios) for value in pair)
        width_monotonic_decrease = all(later < earlier for earlier, later in zip(widths, widths[1:]))
        height_monotonic_decrease = all(later < earlier for earlier, later in zip(heights, heights[1:]))
        aspect_ratio_stable = all(abs(aspect_ratio - expected_aspect_ratio) <= 1.0e-6 for aspect_ratio in aspect_ratios)
        drift_width_monotonic_decrease = all(later < earlier for earlier, later in zip(drift_widths, drift_widths[1:]))
        drift_height_monotonic_decrease = all(later < earlier for earlier, later in zip(drift_heights, drift_heights[1:]))
        drift_aspect_ratio_stable = all(abs(aspect_ratio - expected_aspect_ratio) <= 1.0e-6 for aspect_ratio in drift_aspect_ratios)
        no_reset_to_normal_floor = max(widths[1:], default=widths[0]) < widget._SKY_VIEW_MIN_FIELD_WIDTH_DEG
        drift_no_reset_to_normal_floor = max(drift_widths[1:], default=drift_widths[0]) < widget._SKY_VIEW_MIN_FIELD_WIDTH_DEG
        return {
            "sequence": sequence,
            "drift_without_selected_moon_sequence": drift_sequence,
            "finite": finite,
            "width_monotonic_decrease": width_monotonic_decrease,
            "height_monotonic_decrease": height_monotonic_decrease,
            "aspect_ratio_stable": aspect_ratio_stable,
            "drift_width_monotonic_decrease": drift_width_monotonic_decrease,
            "drift_height_monotonic_decrease": drift_height_monotonic_decrease,
            "drift_aspect_ratio_stable": drift_aspect_ratio_stable,
            "expected_aspect_ratio": expected_aspect_ratio,
            "no_reset_to_normal_floor": no_reset_to_normal_floor,
            "drift_no_reset_to_normal_floor": drift_no_reset_to_normal_floor,
        }
    finally:
        widget.close()
        widget.deleteLater()
        app.processEvents()


def _moon_phase5d_overlay_alignment_report() -> dict[str, object]:
    from photometry_app.ui.main_window import _SkyAtlasViewWidget, _SkyVisibleObject

    app = QApplication.instance() or QApplication([])
    widget = _SkyAtlasViewWidget()
    try:
        widget.resize(640, 420)
        rect = QRectF(0.0, 0.0, 640.0, 420.0)
        moon = SkyAtlasObject(
            name="Moon",
            object_type="Natural Satellite",
            ra_deg=120.0,
            dec_deg=14.0,
            magnitude=-12.2,
            catalog="Solar System",
            searchable=True,
            label_visible=True,
            selectable=True,
        )
        widget.set_selected_object(moon)
        widget._field_width_deg = 0.3
        widget._sky_view_moon_last_draw_state = MoonDrawState(
            viewport_width_px=640,
            viewport_height_px=420,
            center_x_px=320.0,
            center_y_px=210.0,
            radius_px=88.0,
            opacity=1.0,
            sun_direction_moon_local=(0.0, 0.0, 1.0),
            view_to_moon_matrix=((1.0, 0.0, 0.0), (0.0, 1.0, 0.0), (0.0, 0.0, 1.0)),
            selected_lod_label="smoke",
        )
        widget._sky_view_moon_active_texture_mode = "global_albedo_global_normal"
        image = QImage(640, 420, QImage.Format.Format_ARGB32_Premultiplied)
        image.fill(QColor(0, 0, 0, 0))
        painter = QPainter(image)
        try:
            widget._draw_object_overlays(
                painter,
                [_SkyVisibleObject(moon, 520.0, 210.0, 1.0, moon.magnitude, 45.0)],
                rect,
                draw_star_sprites=False,
                ground_geometry=None,
            )
        finally:
            painter.end()
        report = widget._moon_visual_debug_snapshot(rect)
        report["projected_object_count"] = len(widget._last_projected_objects)
        return report
    finally:
        widget.close()
        widget.deleteLater()
        app.processEvents()


def _moon_phase5d_debug_modes_report() -> dict[str, object]:
    from photometry_app.ui.main_window import _SkyAtlasViewWidget

    app = QApplication.instance() or QApplication([])
    widget = _SkyAtlasViewWidget()
    try:
        return {
            "available_render_modes": {
                mode: MoonRendererGL.debug_render_mode_code(mode)
                for mode in (
                    "normal",
                    "albedo_only",
                    "lighting_only",
                    "normal_visualization",
                    "uv_debug",
                    "tile_source_route",
                    "global_tiled_difference",
                    "polar_cap_route",
                    "polar_cap_footprint",
                )
            },
            "default_render_mode": widget.moon_debug_render_mode,
            "invalid_render_mode_fallback": widget._normalize_moon_debug_render_mode("unexpected"),
            "disable_tiled_textures_default": widget.moon_debug_disable_tiled_textures,
            "force_global_texture_only_default": widget.moon_debug_force_global_texture_only,
            "disable_terrain_normals_default": widget.moon_debug_disable_terrain_normals,
        }
    finally:
        widget.close()
        widget.deleteLater()
        app.processEvents()


def _moon_phase5e_debug_controls_report() -> dict[str, object]:
    from photometry_app.ui.main_window import _SkyAtlasViewWidget

    app = QApplication.instance() or QApplication([])
    widget = _SkyAtlasViewWidget()
    try:
        rect = QRectF(0.0, 0.0, 640.0, 420.0)
        render_mode_codes = {
            mode: MoonRendererGL.debug_render_mode_code(mode)
            for mode in (
                "normal",
                "albedo_only",
                "lighting_only",
                "normal_visualization",
                "uv_debug",
                "tile_source_route",
                "global_tiled_difference",
                "polar_cap_route",
                "polar_cap_footprint",
            )
        }
        widget.set_moon_debug_options(
            debug_render_mode="albedo_only",
            overlay_enabled=True,
            force_global_texture_only=True,
        )
        global_snapshot = widget._moon_visual_debug_snapshot(rect)
        widget.set_moon_debug_options(
            force_global_texture_only=False,
            force_tiled_texture_only=True,
            disable_tone_mapping=True,
        )
        tiled_snapshot = widget._moon_visual_debug_snapshot(rect)
        widget.set_moon_debug_options(debug_render_mode="normal_visualization", force_tiled_texture_only=False)
        normal_visualization_snapshot = widget._moon_visual_debug_snapshot(rect)
        widget.set_moon_debug_options(force_global_texture_only=True, force_tiled_texture_only=True)
        mutual_exclusion_state = {
            "overlay_enabled": widget.moon_debug_overlay_enabled,
            "force_global_texture_only": widget.moon_debug_force_global_texture_only,
            "force_tiled_texture_only": widget.moon_debug_force_tiled_texture_only,
            "disable_tiled_textures": widget.moon_debug_disable_tiled_textures,
            "disable_tone_mapping": widget.moon_debug_disable_tone_mapping,
        }
        return {
            "bright_ray_smear_debug_sequence": [
                "albedo_only + global_texture_only",
                "albedo_only + tiled_texture_if_available",
                "normal_disabled",
                "normal_visualization",
                "lighting_only",
                "tile_source_route",
                "global_tiled_difference",
                "polar_cap_route",
                "polar_cap_footprint",
                "normal",
            ],
            "render_mode_codes": render_mode_codes,
            "has_expected_render_modes": sorted(render_mode_codes.values()) == [0, 1, 2, 3, 4, 5, 6, 7, 8],
            "global_isolation_snapshot": {
                "debug_render_mode": global_snapshot["debug_render_mode"],
                "source_mix_mode": global_snapshot["source_mix_mode"],
            },
            "tiled_isolation_snapshot": {
                "debug_render_mode": tiled_snapshot["debug_render_mode"],
                "source_mix_mode": tiled_snapshot["source_mix_mode"],
                "tone_mapping_disabled": tiled_snapshot["tone_mapping_disabled"],
            },
            "normal_visualization_snapshot": {
                "debug_render_mode": normal_visualization_snapshot["debug_render_mode"],
                "source_mix_mode": normal_visualization_snapshot["source_mix_mode"],
            },
            "mutual_exclusion_state": mutual_exclusion_state,
            "tile_isolation_request_preserved": tiled_snapshot["source_mix_mode"] == "tiled_only_if_available",
            "tone_mapping_toggle_preserved": bool(tiled_snapshot["tone_mapping_disabled"]),
        }
    finally:
        widget.close()
        widget.deleteLater()
        app.processEvents()


def _moon_phase5f_high_zoom_source_report() -> dict[str, object]:
    from photometry_app.ui.main_window import _SkyAtlasViewWidget

    app = QApplication.instance() or QApplication([])
    widget = _SkyAtlasViewWidget()
    try:
        widget.resize(640, 420)
        rect = QRectF(0.0, 0.0, 640.0, 420.0)
        moon = SkyAtlasObject(
            name="Moon",
            object_type="Natural Satellite",
            ra_deg=120.0,
            dec_deg=14.0,
            magnitude=-12.2,
            catalog="Solar System",
            searchable=True,
            label_visible=True,
            selectable=True,
        )
        widget.set_selected_object(moon)
        widget._field_width_deg = 0.28
        widget._sky_view_moon_last_draw_state = MoonDrawState(
            viewport_width_px=640,
            viewport_height_px=420,
            center_x_px=320.0,
            center_y_px=210.0,
            radius_px=280.0,
            opacity=1.0,
            sun_direction_moon_local=(0.0, 0.0, 1.0),
            view_to_moon_matrix=((1.0, 0.0, 0.0), (0.0, 1.0, 0.0), (0.0, 0.0, 1.0)),
            selected_lod_label="smoke",
        )
        widget._sky_view_moon_tile_diagnostics = MoonTileDiagnostics.inactive("moon_tile_directory_unavailable").with_render_path_state(
            global_albedo_active=True,
            global_normal_active=True,
            tile_bindings=(),
        )
        snapshot = widget._moon_visual_debug_snapshot(rect)
        lighting_samples = {
            str(sample): MoonRendererGL.lighting_scalar_value(sample)
            for sample in (0.0, 0.2, 0.5, 0.8, 1.0)
        }
        lighting_values = tuple(float(value) for value in lighting_samples.values())
        return {
            "moon_diameter_px": snapshot["moon_diameter_px"],
            "suppression_threshold_px": snapshot["suppression_threshold_px"],
            "background_suppression_scalar": snapshot["background_suppression_scalar"],
            "moon_focused_for_background_suppression": snapshot["moon_focused_for_background_suppression"],
            "high_zoom_global_texture_warning": snapshot["high_zoom_global_texture_warning"],
            "tile_fallback_reason": snapshot["tile_fallback_reason"],
            "albedo_remap_settings": {
                "albedo_brightness": snapshot["albedo_brightness"],
                "albedo_contrast": snapshot["albedo_contrast"],
                "albedo_saturation_retention": snapshot["albedo_saturation_retention"],
                "mare_highland_contrast_boost": snapshot["mare_highland_contrast_boost"],
            },
            "lighting_diagnostics": {
                "highlight_knee": snapshot["lighting_highlight_knee"],
                "highlight_compression": snapshot["lighting_highlight_compression"],
                "samples": lighting_samples,
                "non_uniform_white": max(lighting_values) < 0.9 and (max(lighting_values) - min(lighting_values)) > 0.18,
            },
            "finite": all(math.isfinite(float(value)) for value in lighting_values)
            and math.isfinite(float(snapshot["moon_diameter_px"]))
            and math.isfinite(float(snapshot["background_suppression_scalar"])),
        }
    finally:
        widget.close()
        widget.deleteLater()
        app.processEvents()


def build_moon_phase5g_real_tile_assets_report(project_root: Path, *, output_dir: Path | None = None) -> dict[str, object]:
    resolved_root = Path(project_root).resolve()
    resolved_output_dir = Path(output_dir).resolve() if output_dir is not None else resolved_root / "assets" / "moon_tiles"
    validation_report = validate_moon_tile_tree(resolved_root, output_dir=resolved_output_dir)
    manager_compatibility = validation_report.get("manager_compatibility", {})
    real_tile_assets_present = bool(manager_compatibility.get("scan_albedo_count", 0))
    report: dict[str, object] = {
        "real_tile_assets_present": real_tile_assets_present,
        "real_tiled_shader_active": False,
        "resident_real_albedo_tile_count": 0,
        "resident_real_normal_tile_count": 0,
        "selected_real_tile_keys": [],
        "high_zoom_global_texture_warning": True,
        "visible_tile_count": 0,
        "selected_tile_count": 0,
        "bound_tile_count": 0,
        "visible_coverage_estimate": 0.0,
        "fallback_coverage_estimate": 1.0,
        "active_lod_level": -1,
        "tile_lod_luminance_consistency": validation_report.get("tile_lod_luminance_consistency", {}),
        "tile_uv_validation_status": validation_report.get("tile_uv_validation", {}).get("status", "missing"),
        "tiled_normal_validation_status": validation_report.get("tiled_normal_validation", {}).get("status", "missing"),
        "tiled_normal_disabled_fallback": False,
        "real_tile_validation": validation_report,
    }
    if not real_tile_assets_present:
        return report

    registration_report = validation_report.get("global_vs_tiled_registration", {})
    registered_global_path_value = registration_report.get("global_source_path") if isinstance(registration_report, dict) else None
    registered_global_path = Path(str(registered_global_path_value)) if registered_global_path_value else None
    cache = MoonCache(
        resolved_root,
        color_texture_path=registered_global_path if registered_global_path is not None and registered_global_path.is_file() else None,
        executor=_ImmediateExecutor(),  # type: ignore[arg-type]
        albedo_tile_root=resolved_output_dir / "albedo",
        normal_tile_root=resolved_output_dir / "normal",
    )
    try:
        case = next(case for case in DEFAULT_MOON_VISUAL_SMOKE_CASES if case.expected_phase == "high-zoom-first-quarter")
        ephemeris = MoonEphemeris(cache)
        state = ephemeris.state_at(case.observation_time_utc, case.latitude_deg, case.longitude_deg, 0.0)
        draw_state = _smoke_moon_draw_state(state, case.field_width_deg)
        cache.request_texture_lods()
        cache.request_normal_texture_lods()
        global_albedo_lod = cache.best_available_texture_lod(draw_state.diameter_px)
        global_normal_lod = cache.best_available_normal_texture_lod(draw_state.diameter_px)
        tile_diagnostics = cache.prepare_tiled_textures(
            draw_state,
            state,
            normal_maps_active=True,
            global_albedo_lod=global_albedo_lod,
            global_normal_lod=global_normal_lod,
        )
        tile_diagnostics = _diagnostics_with_render_paths(cache, draw_state, tile_diagnostics, normal_maps_active=True)
        resident_albedo_tiles = sum(1 for key in cache.tile_manager._resident_tiles if key.kind == "albedo")
        resident_normal_tiles = sum(1 for key in cache.tile_manager._resident_tiles if key.kind == "normal")
        selected_real_tile_keys = list(tile_diagnostics.selected_bound_tile_keys or tile_diagnostics.selected_tile_keys)
        report.update(
            {
                "real_tiled_shader_active": bool(tile_diagnostics.tiled_shader_active and tile_diagnostics.bound_albedo_tile_count > 0),
                "resident_real_albedo_tile_count": int(resident_albedo_tiles),
                "resident_real_normal_tile_count": int(resident_normal_tiles),
                "selected_real_tile_keys": selected_real_tile_keys,
                "high_zoom_global_texture_warning": not bool(tile_diagnostics.tiled_shader_active and tile_diagnostics.bound_albedo_tile_count > 0),
                "visible_tile_count": int(tile_diagnostics.visible_tile_count),
                "selected_tile_count": int(tile_diagnostics.selected_tile_count),
                "bound_tile_count": int(tile_diagnostics.bound_tile_count),
                "visible_coverage_estimate": float(tile_diagnostics.visible_coverage_estimate),
                "fallback_coverage_estimate": float(tile_diagnostics.fallback_coverage_estimate),
                "active_lod_level": int(tile_diagnostics.tile_lod_level),
                "tiled_normal_disabled_fallback": bool(tile_diagnostics.tiled_normal_disabled_fallback),
                "tile_diagnostics": tile_diagnostics.as_dict(),
            }
        )
        return report
    finally:
        cache.shutdown()


def build_moon_phase5j_runtime_transition_report(project_root: Path, *, output_dir: Path | None = None) -> dict[str, object]:
    resolved_root = Path(project_root).resolve()
    resolved_output_dir = Path(output_dir).resolve() if output_dir is not None else resolved_root / "assets" / "moon_tiles"
    validation_report = validate_moon_tile_tree(resolved_root, output_dir=resolved_output_dir)
    registration_report = validation_report.get("global_vs_tiled_registration", {})
    global_path_value = registration_report.get("global_source_path") if isinstance(registration_report, dict) else None
    global_path = Path(str(global_path_value)) if global_path_value else None
    cache = MoonCache(
        resolved_root,
        color_texture_path=global_path if global_path is not None and global_path.is_file() else None,
        executor=_ImmediateExecutor(),  # type: ignore[arg-type]
        albedo_tile_root=resolved_output_dir / "albedo",
        normal_tile_root=resolved_output_dir / "normal",
    )
    reports: list[dict[str, object]] = []
    try:
        case = next(case for case in DEFAULT_MOON_VISUAL_SMOKE_CASES if case.expected_phase == "high-zoom-first-quarter")
        state = MoonEphemeris(cache).state_at(case.observation_time_utc, case.latitude_deg, case.longitude_deg, 0.0)
        for field_width_deg, field_height_deg in ((1.2, 0.6), (1.1, 0.5)):
            draw_state = _smoke_moon_draw_state(state, field_width_deg, viewport_px=2000)
            cache.request_texture_lods()
            global_albedo_lod = cache.best_available_texture_lod(draw_state.diameter_px)
            diagnostics = cache.prepare_tiled_textures(
                draw_state,
                state,
                normal_maps_active=False,
                global_albedo_lod=global_albedo_lod,
            )
            diagnostics = _diagnostics_with_render_paths(cache, draw_state, diagnostics, normal_maps_active=False)
            reports.append(
                {
                    "field_width_deg": field_width_deg,
                    "field_height_deg": field_height_deg,
                    "diameter_px": draw_state.diameter_px,
                    "active_source_mode": diagnostics.active_source_mode,
                    "bound_tile_keys": list(diagnostics.bound_tile_keys),
                    "tile_cache_generation_id": diagnostics.tile_cache_generation_id,
                    "manifest_hash": diagnostics.manifest_hash,
                    "visible_coverage_estimate": diagnostics.visible_coverage_estimate,
                    "fallback_coverage_estimate": diagnostics.fallback_coverage_estimate,
                    "candidate_visible_coverage_estimate": diagnostics.candidate_visible_coverage_estimate,
                    "live_equivalence_status": diagnostics.live_equivalence_status,
                    "live_probe_max_color_delta": diagnostics.live_probe_max_color_delta,
                    "landmark_sample_deltas": list(diagnostics.live_landmark_sample_deltas),
                    "out_of_bounds_tile_sampling": diagnostics.any_out_of_bounds_tile_sampling,
                    "fallback_reason": diagnostics.fallback_reason,
                }
            )
    finally:
        cache.shutdown()
    zoomed_report = reports[-1] if reports else {}
    transition_safe = bool(
        reports
        and not any(bool(report["out_of_bounds_tile_sampling"]) for report in reports)
        and (
            zoomed_report.get("active_source_mode") == "fallback"
            or (
                zoomed_report.get("active_source_mode") == "tiled"
                and zoomed_report.get("live_equivalence_status") == "pass"
                and float(zoomed_report.get("visible_coverage_estimate", 0.0)) >= 0.999
            )
        )
    )
    return {"views": reports, "transition_safe": transition_safe}


def build_moon_phase5k_normal_continuity_report(project_root: Path, *, output_dir: Path | None = None) -> dict[str, object]:
    resolved_root = Path(project_root).resolve()
    resolved_output_dir = Path(output_dir).resolve() if output_dir is not None else resolved_root / "assets" / "moon_tiles"
    validation_report = validate_moon_tile_tree(resolved_root, output_dir=resolved_output_dir)
    tile_assets_present = bool(validation_report.get("manager_compatibility", {}).get("scan_albedo_count", 0))
    registration_report = validation_report.get("global_vs_tiled_registration", {})
    global_path_value = registration_report.get("global_source_path") if isinstance(registration_report, dict) else None
    global_path = Path(str(global_path_value)) if global_path_value else None
    manifest: dict[str, object] = {}
    try:
        manifest = json.loads((resolved_output_dir / "build_manifest.json").read_text(encoding="utf-8"))
    except (OSError, ValueError, TypeError):
        pass
    manifest_normal = manifest.get("normal", {}) if isinstance(manifest, dict) else {}
    normal_source_value = manifest_normal.get("source_path") if isinstance(manifest_normal, dict) else None
    normal_source_kind = str(manifest_normal.get("source_kind") or "") if isinstance(manifest_normal, dict) else ""
    normal_source_path = Path(str(normal_source_value)) if normal_source_value else None
    cache = MoonCache(
        resolved_root,
        color_texture_path=global_path if global_path is not None and global_path.is_file() else None,
        normal_texture_path=normal_source_path if normal_source_kind == "normal" and normal_source_path is not None and normal_source_path.is_file() else None,
        displacement_texture_path=normal_source_path if normal_source_kind == "height" and normal_source_path is not None and normal_source_path.is_file() else None,
        executor=_ImmediateExecutor(),  # type: ignore[arg-type]
        albedo_tile_root=resolved_output_dir / "albedo",
        normal_tile_root=resolved_output_dir / "normal",
    )
    view_reports: list[dict[str, object]] = []
    mode_reports: list[dict[str, object]] = []
    try:
        case = next(case for case in DEFAULT_MOON_VISUAL_SMOKE_CASES if case.expected_phase == "high-zoom-first-quarter")
        state = MoonEphemeris(cache).state_at(case.observation_time_utc, case.latitude_deg, case.longitude_deg, 0.0)
        cache.request_texture_lods()
        cache.request_normal_texture_lods()
        for field_width_deg, field_height_deg in ((1.2, 0.6), (1.1, 0.5)):
            draw_state = _smoke_moon_draw_state(state, field_width_deg, viewport_px=2000)
            global_albedo_lod = cache.best_available_texture_lod(draw_state.diameter_px)
            global_normal_lod = cache.best_available_normal_texture_lod(draw_state.diameter_px)
            diagnostics = cache.prepare_tiled_textures(
                draw_state,
                state,
                normal_maps_active=True,
                global_albedo_lod=global_albedo_lod,
                global_normal_lod=global_normal_lod,
            )
            diagnostics = _diagnostics_with_render_paths(cache, draw_state, diagnostics, normal_maps_active=True)
            view_reports.append(
                {
                    "field_width_deg": field_width_deg,
                    "field_height_deg": field_height_deg,
                    "diameter_px": draw_state.diameter_px,
                    "active_source_mode": diagnostics.active_source_mode,
                    "active_normal_source": diagnostics.active_normal_source,
                    "tiled_normal_fade_factor": diagnostics.tiled_normal_fade_factor,
                    "terminator_normal_factor": diagnostics.terminator_normal_factor,
                    "bound_tile_keys": list(diagnostics.bound_tile_keys),
                    "problematic_tile_key": diagnostics.problematic_tile_key,
                    "max_normal_slope": diagnostics.max_normal_slope,
                    "normal_artifact_risk": diagnostics.normal_artifact_risk,
                    "normal_tile_statistics": [dict(item) for item in diagnostics.normal_tile_statistics],
                    "artifact_lunar_latitude_deg": diagnostics.artifact_lunar_latitude_deg,
                    "artifact_lunar_longitude_deg": diagnostics.artifact_lunar_longitude_deg,
                    "artifact_disc_position": diagnostics.artifact_disc_position,
                    "visible_pole_proximity": diagnostics.visible_pole_proximity,
                    "visible_seam_proximity": diagnostics.visible_seam_proximity,
                    "visible_uv_near_north_pole": diagnostics.visible_uv_near_north_pole,
                    "visible_uv_near_south_pole": diagnostics.visible_uv_near_south_pole,
                    "visible_uv_crosses_longitude_seam": diagnostics.visible_uv_crosses_longitude_seam,
                    "polar_normal_fade_factor": diagnostics.polar_normal_fade_factor,
                    "polar_albedo_fade_factor": diagnostics.polar_albedo_fade_factor,
                    "polar_analytic_normal_active": diagnostics.polar_analytic_normal_active,
                    "seam_wrap_active": diagnostics.seam_wrap_active,
                    "pole_singularity_guard_active": diagnostics.pole_singularity_guard_active,
                    "polar_cap_active": diagnostics.polar_cap_active,
                    "north_polar_cap_available": diagnostics.north_polar_cap_available,
                    "south_polar_cap_available": diagnostics.south_polar_cap_available,
                    "polar_cap_normal_available": diagnostics.polar_cap_normal_available,
                    "polar_cap_blend_factor": diagnostics.polar_cap_blend_factor,
                    "polar_cap_projection": diagnostics.polar_cap_projection,
                    "visible_pole_latitude": diagnostics.visible_pole_latitude,
                    "polar_cap_fallback_reason": diagnostics.polar_cap_fallback_reason,
                    "polar_cap_size_px": diagnostics.polar_cap_size_px,
                    "polar_cap_blend_start_lat_deg": diagnostics.polar_cap_blend_start_lat_deg,
                    "polar_cap_blend_end_lat_deg": diagnostics.polar_cap_blend_end_lat_deg,
                    "polar_cap_blend_curve_power": diagnostics.polar_cap_blend_curve_power,
                    "polar_cap_boundary_luma_delta": diagnostics.polar_cap_boundary_luma_delta,
                    "polar_cap_boundary_color_delta": diagnostics.polar_cap_boundary_color_delta,
                    "polar_cap_boundary_contrast_delta": diagnostics.polar_cap_boundary_contrast_delta,
                    "polar_cap_core_sharpness_estimate": diagnostics.polar_cap_core_sharpness_estimate,
                    "polar_cap_core_vs_surrounding_detail_delta": diagnostics.polar_cap_core_vs_surrounding_detail_delta,
                    "polar_cap_normal_blend_factor": diagnostics.polar_cap_normal_blend_factor,
                    "polar_cap_filtering": diagnostics.polar_cap_filtering,
                    "polar_cap_ring_risk": diagnostics.polar_cap_ring_risk,
                    "polar_cap_footprint_risk": diagnostics.polar_cap_footprint_risk,
                }
            )

        draw_state = _smoke_moon_draw_state(state, 1.1, viewport_px=2000)
        global_albedo_lod = cache.best_available_texture_lod(draw_state.diameter_px)
        global_normal_lod = cache.best_available_normal_texture_lod(draw_state.diameter_px)

        for mode_name, request_tile_normals, use_global_normal, fade_override in (
            ("tiled_albedo_tiled_normal_unpolished", True, True, 1.0),
            ("tiled_albedo_global_normal", False, True, 0.0),
            ("tiled_albedo_no_normal", False, False, 0.0),
        ):
            mode_draw_state = replace(draw_state, tiled_normal_fade_factor=fade_override)
            diagnostics = cache.prepare_tiled_textures(
                mode_draw_state,
                state,
                normal_maps_active=request_tile_normals,
                global_albedo_lod=global_albedo_lod,
                global_normal_lod=global_normal_lod if use_global_normal else None,
            )
            bindings = cache.moon_tile_shader_bindings(normal_maps_active=request_tile_normals)
            diagnostics = diagnostics.with_render_path_state(
                global_albedo_active=True,
                global_normal_active=bool(use_global_normal and global_normal_lod is not None),
                tile_bindings=bindings,
                tiled_normal_strength=mode_draw_state.tiled_normal_strength,
                tiled_normal_fade_factor=mode_draw_state.tiled_normal_fade_factor,
                terminator_normal_factor=mode_draw_state.terminator_normal_factor,
            )
            artifact_risk_visible = bool(
                diagnostics.normal_artifact_risk
                and diagnostics.active_normal_source == "tiled"
                and diagnostics.tiled_normal_fade_factor > 0.0
            )
            mode_reports.append(
                {
                    "mode": mode_name,
                    "active_source_mode": diagnostics.active_source_mode,
                    "active_normal_source": diagnostics.active_normal_source,
                    "tiled_normal_fade_factor": diagnostics.tiled_normal_fade_factor,
                    "artifact_risk_visible": artifact_risk_visible,
                    "problematic_tile_key": diagnostics.problematic_tile_key,
                    "max_normal_slope": diagnostics.max_normal_slope,
                }
            )
        mode_reports.append(
            {
                "mode": "global_albedo_global_normal",
                "active_source_mode": "global",
                "active_normal_source": "global" if global_normal_lod is not None else "none",
                "tiled_normal_fade_factor": 0.0,
                "artifact_risk_visible": False,
                "problematic_tile_key": "",
                "max_normal_slope": None,
            }
        )
        default_zoomed = view_reports[-1]
        return {
            "tile_assets_present": tile_assets_present,
            "views": view_reports,
            "diagnostic_modes_at_1_1": mode_reports,
            "default_transition_suppresses_tiled_normal": bool(
                not tile_assets_present
                or (
                    default_zoomed["active_source_mode"] == "tiled"
                    and default_zoomed["active_normal_source"] in {"global", "none"}
                    and float(default_zoomed["tiled_normal_fade_factor"]) <= 1.0e-6
                )
            ),
        }
    finally:
        cache.shutdown()


def _phase5l_polar_guard_from_normal_continuity_report(normal_continuity: dict[str, object]) -> dict[str, object]:
    views = list(normal_continuity.get("views", []))
    polar_views = [
        {
            "field_width_deg": view["field_width_deg"],
            "field_height_deg": view["field_height_deg"],
            "active_source_mode": view["active_source_mode"],
            "active_normal_source": view["active_normal_source"],
            "visible_pole_proximity": view["visible_pole_proximity"],
            "visible_seam_proximity": view["visible_seam_proximity"],
            "visible_uv_near_north_pole": view["visible_uv_near_north_pole"],
            "visible_uv_near_south_pole": view["visible_uv_near_south_pole"],
            "visible_uv_crosses_longitude_seam": view["visible_uv_crosses_longitude_seam"],
            "artifact_lunar_latitude_deg": view["artifact_lunar_latitude_deg"],
            "artifact_lunar_longitude_deg": view["artifact_lunar_longitude_deg"],
            "artifact_disc_position": view["artifact_disc_position"],
            "polar_normal_fade_factor": view["polar_normal_fade_factor"],
            "polar_albedo_fade_factor": view["polar_albedo_fade_factor"],
            "polar_analytic_normal_active": view["polar_analytic_normal_active"],
            "seam_wrap_active": view["seam_wrap_active"],
            "pole_singularity_guard_active": view["pole_singularity_guard_active"],
        }
        for view in views
    ]
    guarded_view = max(polar_views, key=lambda view: float(view["visible_pole_proximity"]), default={})
    return {
        "views": polar_views,
        "visible_artifact_is_polar": bool(
            guarded_view
            and float(guarded_view["visible_pole_proximity"]) > 0.0
            and bool(guarded_view["pole_singularity_guard_active"])
        ),
        "seam_wrap_active": bool(all(bool(view["seam_wrap_active"]) for view in polar_views)),
        "guarded_view": guarded_view,
    }


def build_moon_phase5l_polar_guard_report(project_root: Path, *, output_dir: Path | None = None) -> dict[str, object]:
    return _phase5l_polar_guard_from_normal_continuity_report(
        build_moon_phase5k_normal_continuity_report(project_root, output_dir=output_dir)
    )


def build_moon_phase5m_polar_cap_report(project_root: Path, *, output_dir: Path | None = None) -> dict[str, object]:

    normal_continuity = build_moon_phase5k_normal_continuity_report(project_root, output_dir=output_dir)
    views = [
        {
            "field_width_deg": view["field_width_deg"],
            "field_height_deg": view["field_height_deg"],
            "active_source_mode": view["active_source_mode"],
            "active_normal_source": view["active_normal_source"],
            "polar_cap_active": view["polar_cap_active"],
            "north_polar_cap_available": view["north_polar_cap_available"],
            "south_polar_cap_available": view["south_polar_cap_available"],
            "polar_cap_normal_available": view["polar_cap_normal_available"],
            "polar_cap_blend_factor": view["polar_cap_blend_factor"],
            "polar_cap_projection": view["polar_cap_projection"],
            "visible_pole_latitude": view["visible_pole_latitude"],
            "polar_cap_fallback_reason": view["polar_cap_fallback_reason"],
            "bound_tile_keys": view["bound_tile_keys"],
        }
        for view in normal_continuity.get("views", [])
    ]
    south_pole_views = [view for view in views if float(view["visible_pole_latitude"] or 0.0) < -75.0]
    return {
        "views": views,
        "south_polar_cap_routes_visible_artifact": bool(
            south_pole_views
            and all(bool(view["polar_cap_active"]) for view in south_pole_views)
            and all(float(view["polar_cap_blend_factor"]) > 0.0 for view in south_pole_views)
        ),
        "fallback_is_safe": bool(
            all(
                bool(view["polar_cap_active"]) or bool(view["polar_cap_fallback_reason"])
                for view in south_pole_views
            )
        ),
    }


def build_moon_phase5n_polar_cap_blend_report(project_root: Path, *, output_dir: Path | None = None) -> dict[str, object]:

    normal_continuity = build_moon_phase5k_normal_continuity_report(project_root, output_dir=output_dir)
    views = [
        {
            "field_width_deg": view["field_width_deg"],
            "field_height_deg": view["field_height_deg"],
            "active_source_mode": view["active_source_mode"],
            "polar_cap_active": view["polar_cap_active"],
            "polar_cap_size_px": view["polar_cap_size_px"],
            "polar_cap_blend_start_lat_deg": view["polar_cap_blend_start_lat_deg"],
            "polar_cap_blend_end_lat_deg": view["polar_cap_blend_end_lat_deg"],
            "polar_cap_blend_factor": view["polar_cap_blend_factor"],
            "polar_cap_boundary_luma_delta": view["polar_cap_boundary_luma_delta"],
            "polar_cap_boundary_color_delta": view["polar_cap_boundary_color_delta"],
            "polar_cap_boundary_contrast_delta": view["polar_cap_boundary_contrast_delta"],
            "polar_cap_core_sharpness_estimate": view["polar_cap_core_sharpness_estimate"],
            "polar_cap_core_vs_surrounding_detail_delta": view["polar_cap_core_vs_surrounding_detail_delta"],
            "polar_cap_normal_blend_factor": view["polar_cap_normal_blend_factor"],
            "polar_cap_filtering": view["polar_cap_filtering"],
            "polar_cap_ring_risk": view["polar_cap_ring_risk"],
            "polar_cap_footprint_risk": view["polar_cap_footprint_risk"],
        }
        for view in normal_continuity.get("views", [])
    ]
    active_views = [view for view in views if bool(view["polar_cap_active"])]
    return {
        "views": views,
        "boundary_match_bounded": bool(
            all(float(view["polar_cap_boundary_luma_delta"] or 0.0) <= 0.02 for view in active_views)
            and all(float(view["polar_cap_boundary_color_delta"] or 0.0) <= 0.02 for view in active_views)
        ),
        "normal_blend_is_conservative": bool(
            all(
                float(view["polar_cap_normal_blend_factor"]) <= float(view["polar_cap_blend_factor"]) + 1.0e-9
                for view in active_views
            )
        ),
        "ring_risk_clear": bool(all(not bool(view["polar_cap_ring_risk"]) for view in active_views)),
    }


def build_moon_phase5o_polar_cap_integration_report(project_root: Path, *, output_dir: Path | None = None) -> dict[str, object]:

    normal_continuity = build_moon_phase5k_normal_continuity_report(project_root, output_dir=output_dir)
    views = [
        {
            "field_width_deg": view["field_width_deg"],
            "field_height_deg": view["field_height_deg"],
            "active_source_mode": view["active_source_mode"],
            "polar_cap_active": view["polar_cap_active"],
            "polar_cap_size_px": view["polar_cap_size_px"],
            "polar_cap_core_sharpness_estimate": view["polar_cap_core_sharpness_estimate"],
            "polar_cap_boundary_luma_delta": view["polar_cap_boundary_luma_delta"],
            "polar_cap_boundary_contrast_delta": view["polar_cap_boundary_contrast_delta"],
            "polar_cap_core_vs_surrounding_detail_delta": view["polar_cap_core_vs_surrounding_detail_delta"],
            "polar_cap_filtering": view["polar_cap_filtering"],
            "polar_cap_footprint_risk": view["polar_cap_footprint_risk"],
        }
        for view in normal_continuity.get("views", [])
    ]
    active_views = [view for view in views if bool(view["polar_cap_active"])]
    return {
        "views": views,
        "boundary_contrast_bounded": bool(
            all(float(view["polar_cap_boundary_contrast_delta"] or 0.0) <= 0.18 for view in active_views)
        ),
        "core_detail_is_integrated": bool(
            all(float(view["polar_cap_core_vs_surrounding_detail_delta"] or 0.0) >= -0.16 for view in active_views)
        ),
        "footprint_risk_clear": bool(all(not bool(view["polar_cap_footprint_risk"]) for view in active_views)),
    }


def build_moon_visual_smoke_report(project_root: Path) -> dict[str, object]:
    cache = MoonCache(project_root)
    orientation_selection = select_lunar_orientation_provider(project_root=project_root)
    ephemeris = MoonEphemeris(cache, orientation_provider=orientation_selection.provider)
    scenario_reports: list[dict[str, object]] = []
    orientation_comparison: list[dict[str, object]] = []
    moon_focus_zoom_sequence = _moon_focus_zoom_sequence_report()
    moon_phase5d_overlay_alignment = _moon_phase5d_overlay_alignment_report()
    moon_phase5d_debug_modes = _moon_phase5d_debug_modes_report()
    moon_phase5e_debug_controls = _moon_phase5e_debug_controls_report()
    moon_phase5f_high_zoom_source = _moon_phase5f_high_zoom_source_report()
    moon_phase5g_real_tile_assets = build_moon_phase5g_real_tile_assets_report(project_root)
    moon_phase5j_runtime_transition = build_moon_phase5j_runtime_transition_report(project_root)
    moon_phase5k_normal_continuity = build_moon_phase5k_normal_continuity_report(project_root)
    moon_phase5l_polar_guard = _phase5l_polar_guard_from_normal_continuity_report(moon_phase5k_normal_continuity)
    moon_phase5m_polar_cap = build_moon_phase5m_polar_cap_report(project_root)
    moon_phase5n_polar_cap_blend = build_moon_phase5n_polar_cap_blend_report(project_root)
    moon_phase5o_polar_cap_integration = build_moon_phase5o_polar_cap_integration_report(project_root)
    moon_tone_settings = {
        "albedo_brightness": DEFAULT_MOON_VISUAL_SETTINGS.albedo_brightness,
        "albedo_contrast": DEFAULT_MOON_VISUAL_SETTINGS.albedo_contrast,
        "albedo_saturation_retention": DEFAULT_MOON_VISUAL_SETTINGS.albedo_saturation_retention,
        "mare_highland_contrast_boost": DEFAULT_MOON_VISUAL_SETTINGS.mare_highland_contrast_boost,
        "lighting_hemisphere_blend": DEFAULT_MOON_VISUAL_SETTINGS.lighting_hemisphere_blend,
        "lighting_hemisphere_gamma": DEFAULT_MOON_VISUAL_SETTINGS.lighting_hemisphere_gamma,
        "lighting_highlight_knee": DEFAULT_MOON_VISUAL_SETTINGS.lighting_highlight_knee,
        "lighting_highlight_compression": DEFAULT_MOON_VISUAL_SETTINGS.lighting_highlight_compression,
        "tone_exposure": DEFAULT_MOON_VISUAL_SETTINGS.tone_exposure,
        "tone_highlight_knee": DEFAULT_MOON_VISUAL_SETTINGS.tone_highlight_knee,
        "tone_highlight_compression": DEFAULT_MOON_VISUAL_SETTINGS.tone_highlight_compression,
        "tone_contrast": DEFAULT_MOON_VISUAL_SETTINGS.tone_contrast,
        "tone_pivot": DEFAULT_MOON_VISUAL_SETTINGS.tone_pivot,
        "tone_saturation": DEFAULT_MOON_VISUAL_SETTINGS.tone_saturation,
        "tone_color_preservation": DEFAULT_MOON_VISUAL_SETTINGS.tone_color_preservation,
        "tone_gamma": DEFAULT_MOON_VISUAL_SETTINGS.tone_gamma,
        "terminator_soft_start": DEFAULT_MOON_VISUAL_SETTINGS.terminator_soft_start,
        "terminator_soft_end": DEFAULT_MOON_VISUAL_SETTINGS.terminator_soft_end,
        "terminator_band_width": DEFAULT_MOON_VISUAL_SETTINGS.terminator_band_width,
        "terminator_softness": DEFAULT_MOON_VISUAL_SETTINGS.terminator_softness,
        "terrain_normal_strength_global": DEFAULT_MOON_VISUAL_SETTINGS.terrain_normal_strength_global,
        "terrain_normal_strength_terminator": DEFAULT_MOON_VISUAL_SETTINGS.terrain_normal_strength_terminator,
        "earthshine_floor_default": DEFAULT_MOON_VISUAL_SETTINGS.earthshine_floor_default,
        "earthshine_directional_gamma": DEFAULT_MOON_VISUAL_SETTINGS.earthshine_directional_gamma,
        "earthshine_max": DEFAULT_MOON_VISUAL_SETTINGS.earthshine_max,
    }
    try:
        cache.request_texture_lods()
        cache.request_normal_texture_lods()
        for case in DEFAULT_MOON_VISUAL_SMOKE_CASES:
            state = ephemeris.state_at(case.observation_time_utc, case.latitude_deg, case.longitude_deg, 0.0)
            atmosphere = MoonAtmosphereModel.atmosphere_for_altitude(state.altitude_deg)
            earthshine = MoonEarthshineModel.earthshine_strength_for_phase(state.illuminated_fraction)
            target_lod_width = cache.texture_lod_for_diameter_px(120.0 if case.field_width_deg >= 5.0 else 720.0)
            tile_draw_state = _smoke_moon_draw_state(state, case.field_width_deg)
            tile_diagnostics = cache.prepare_tiled_textures(
                tile_draw_state,
                state,
                normal_maps_active=case.field_width_deg <= 1.0,
            )
            tile_diagnostics = _diagnostics_with_render_paths(
                cache,
                tile_draw_state,
                tile_diagnostics,
                normal_maps_active=case.field_width_deg <= 1.0,
            )
            scenario_reports.append(
                {
                    "label": case.label,
                    "expected_phase": case.expected_phase,
                    "observation_time_utc": case.observation_time_utc.isoformat(),
                    "field_width_deg": case.field_width_deg,
                    "altitude_deg": state.altitude_deg,
                    "azimuth_deg": state.azimuth_deg,
                    "phase_angle_deg": state.phase_angle_deg,
                    "illuminated_fraction": state.illuminated_fraction,
                    "target_lod_width": target_lod_width,
                    "finite_state": _state_is_finite(state),
                    "earthshine_strength": earthshine,
                    "atmosphere_extinction": atmosphere.extinction,
                    "atmosphere_tint_rgb": atmosphere.tint_rgb,
                    "normal_map_optional": True,
                    "moon_tile_diagnostics": tile_diagnostics.as_dict(),
                }
            )

        if orientation_selection.diagnostics.active_provider == "spice":
            orientation_comparison = [
                {
                    "label": result.case.label,
                    "max_body_axis_delta_deg": result.max_body_axis_delta_deg,
                    "libration_longitude_delta_deg": result.libration_longitude_delta_deg,
                    "libration_latitude_delta_deg": result.libration_latitude_delta_deg,
                    "lunar_north_position_angle_delta_deg": result.lunar_north_position_angle_delta_deg,
                }
                for result in compare_lunar_orientation_providers(orientation_selection.provider)
            ]
        comparison_summary = _orientation_comparison_summary(orientation_comparison)

        scrub_start = next(case for case in DEFAULT_MOON_VISUAL_SMOKE_CASES if case.expected_phase == "scrub-no-stall")
        scrub_reports = []
        for step in range(0, 13):
            scrub_time = scrub_start.observation_time_utc + timedelta(hours=step * 2)
            state = ephemeris.state_at(scrub_time, scrub_start.latitude_deg, scrub_start.longitude_deg, 0.0)
            tile_diagnostics = cache.prepare_tiled_textures(
                _smoke_moon_draw_state(state, 0.48),
                state,
                normal_maps_active=True,
            )
            tile_diagnostics = _diagnostics_with_render_paths(cache, _smoke_moon_draw_state(state, 0.48), tile_diagnostics, normal_maps_active=True)
            scrub_reports.append(
                {
                    "observation_time_utc": scrub_time.isoformat(),
                    "finite_state": _state_is_finite(state),
                    "phase_angle_deg": state.phase_angle_deg,
                    "illuminated_fraction": state.illuminated_fraction,
                    "libration_longitude_rad": state.libration_longitude_rad,
                    "moon_tile_diagnostics": tile_diagnostics.as_dict(),
                }
            )

        threshold_case = next(case for case in DEFAULT_MOON_VISUAL_SMOKE_CASES if case.expected_phase == "high-zoom-first-quarter")
        threshold_state = ephemeris.state_at(threshold_case.observation_time_utc, threshold_case.latitude_deg, threshold_case.longitude_deg, 0.0)
        threshold_settings = MoonTileSettings()
        rapid_zoom_reports = []
        for diameter_px in (
            threshold_settings.activation_diameter_px - 80.0,
            threshold_settings.activation_diameter_px + 80.0,
            threshold_settings.activation_diameter_px - threshold_settings.hysteresis_px * 0.5,
            threshold_settings.activation_diameter_px - threshold_settings.hysteresis_px - 40.0,
        ):
            draw_state = _smoke_moon_draw_state(threshold_state, threshold_state.angular_diameter_deg / (diameter_px / 1024.0))
            tile_diagnostics = cache.prepare_tiled_textures(draw_state, threshold_state, normal_maps_active=True)
            tile_diagnostics = _diagnostics_with_render_paths(cache, draw_state, tile_diagnostics, normal_maps_active=True)
            rapid_zoom_reports.append(
                {
                    "diameter_px": diameter_px,
                    "moon_tile_diagnostics": tile_diagnostics.as_dict(),
                }
            )
    finally:
        cache.shutdown()

    missing_normal_cache = MoonCache(
        project_root,
        normal_texture_path=project_root / "textures" / "missing_moon_normal.png",
        displacement_texture_path=project_root / "textures" / "missing_moon_ldem.tif",
    )
    try:
        missing_normal_started = missing_normal_cache.request_normal_texture_lods()
        missing_normal_lod = missing_normal_cache.best_available_normal_texture_lod(256.0)
        missing_normal_report = {
            "label": "missing normal-map fallback",
            "request_started": missing_normal_started,
            "normal_lod_ready": missing_normal_lod is not None,
            "error": missing_normal_cache.normal_texture_lod_error,
            "expected_renderer_mode": "albedo-only",
        }
    finally:
        missing_normal_cache.shutdown()

    missing_tile_cache = MoonCache(
        project_root,
        albedo_tile_root=project_root / "assets" / "missing_moon_tiles" / "albedo",
        normal_tile_root=project_root / "assets" / "missing_moon_tiles" / "normal",
    )
    try:
        missing_tile_case = next(case for case in DEFAULT_MOON_VISUAL_SMOKE_CASES if case.expected_phase == "high-zoom-missing-tiles")
        missing_tile_ephemeris = MoonEphemeris(missing_tile_cache, orientation_provider=orientation_selection.provider)
        missing_tile_state = missing_tile_ephemeris.state_at(missing_tile_case.observation_time_utc, missing_tile_case.latitude_deg, missing_tile_case.longitude_deg, 0.0)
        missing_tile_report = missing_tile_cache.prepare_tiled_textures(
            _smoke_moon_draw_state(missing_tile_state, missing_tile_case.field_width_deg),
            missing_tile_state,
            normal_maps_active=True,
        )
        missing_tile_draw_state = _smoke_moon_draw_state(missing_tile_state, missing_tile_case.field_width_deg)
        missing_tile_report = _diagnostics_with_render_paths(missing_tile_cache, missing_tile_draw_state, missing_tile_report, normal_maps_active=True).as_dict()
    finally:
        missing_tile_cache.shutdown()

    with tempfile.TemporaryDirectory() as temp_dir:
        mock_root = Path(temp_dir)
        _write_smoke_tile_level(mock_root, 1, include_normal=True)
        mock_tile_cache = MoonCache(
            mock_root,
            executor=_ImmediateExecutor(),  # type: ignore[arg-type]
            tile_settings=MoonTileSettings(activation_diameter_px=260.0, hysteresis_px=60.0, shader_sampler_limit=4, enforce_source_registration=False),
        )
        try:
            mock_ephemeris = MoonEphemeris(mock_tile_cache, orientation_provider=orientation_selection.provider)
            full_case = next(case for case in DEFAULT_MOON_VISUAL_SMOKE_CASES if case.expected_phase == "high-zoom-full")
            full_state = mock_ephemeris.state_at(full_case.observation_time_utc, full_case.latitude_deg, full_case.longitude_deg, 0.0)
            full_draw_state = _smoke_moon_draw_state(full_state, full_case.field_width_deg)
            full_diag = mock_tile_cache.prepare_tiled_textures(full_draw_state, full_state, normal_maps_active=False)
            full_diag = _diagnostics_with_render_paths(mock_tile_cache, full_draw_state, full_diag, normal_maps_active=False)

            quarter_case = next(case for case in DEFAULT_MOON_VISUAL_SMOKE_CASES if case.expected_phase == "high-zoom-first-quarter")
            quarter_state = mock_ephemeris.state_at(quarter_case.observation_time_utc, quarter_case.latitude_deg, quarter_case.longitude_deg, 0.0)
            quarter_draw_state = _smoke_moon_draw_state(quarter_state, quarter_case.field_width_deg)
            quarter_diag = mock_tile_cache.prepare_tiled_textures(quarter_draw_state, quarter_state, normal_maps_active=True)
            quarter_diag = _diagnostics_with_render_paths(mock_tile_cache, quarter_draw_state, quarter_diag, normal_maps_active=True)
            quarter_bindings = mock_tile_cache.moon_tile_shader_bindings(normal_maps_active=True)
            first_binding = quarter_bindings[0]
            first_bounds = first_binding.uv_bounds
            edge_route_center = MoonRendererGL.tile_sample_route_for_uv(
                (first_bounds.u_min + first_bounds.u_max) * 0.5,
                (first_bounds.v_min + first_bounds.v_max) * 0.5,
                (first_binding,),
            )
            edge_route_near_boundary = MoonRendererGL.tile_sample_route_for_uv(
                first_bounds.u_min + (first_bounds.u_max - first_bounds.u_min) * 0.01,
                (first_bounds.v_min + first_bounds.v_max) * 0.5,
                (first_binding,),
            )
            edge_route_outside = MoonRendererGL.tile_sample_route_for_uv(0.99, 0.01, (first_binding,))

            mock_rapid_zoom_reports = []
            for diameter_px in (220.0, 310.0, 245.0, 180.0):
                draw_state = _smoke_moon_draw_state(quarter_state, quarter_state.angular_diameter_deg / (diameter_px / 1024.0))
                tile_diagnostics = mock_tile_cache.prepare_tiled_textures(draw_state, quarter_state, normal_maps_active=True)
                tile_diagnostics = _diagnostics_with_render_paths(mock_tile_cache, draw_state, tile_diagnostics, normal_maps_active=True)
                mock_rapid_zoom_reports.append(
                    {
                        "diameter_px": diameter_px,
                        "moon_tile_diagnostics": tile_diagnostics.as_dict(),
                    }
                )

            mock_scrub_reports = []
            for step in range(0, 5):
                scrub_time = quarter_case.observation_time_utc + timedelta(hours=step * 3)
                scrub_state = mock_ephemeris.state_at(scrub_time, quarter_case.latitude_deg, quarter_case.longitude_deg, 0.0)
                scrub_draw_state = _smoke_moon_draw_state(scrub_state, 0.45)
                tile_diagnostics = mock_tile_cache.prepare_tiled_textures(scrub_draw_state, scrub_state, normal_maps_active=True)
                tile_diagnostics = _diagnostics_with_render_paths(mock_tile_cache, scrub_draw_state, tile_diagnostics, normal_maps_active=True)
                mock_scrub_reports.append(
                    {
                        "observation_time_utc": scrub_time.isoformat(),
                        "moon_tile_diagnostics": tile_diagnostics.as_dict(),
                    }
                )

            mock_tiled_shader_report = {
                "high_zoom_full_mock_tiles": full_diag.as_dict(),
                "high_zoom_first_quarter_mock_albedo_normal_tiles": quarter_diag.as_dict(),
                "rapid_zoom_tiled_threshold": mock_rapid_zoom_reports,
                "fast_time_scrub_tiled_shader": mock_scrub_reports,
                "tile_edge_fade": {
                    "center_route": _tile_route_dict(edge_route_center),
                    "near_boundary_route": _tile_route_dict(edge_route_near_boundary),
                    "outside_route": _tile_route_dict(edge_route_outside),
                },
            }
        finally:
            mock_tile_cache.shutdown()

    with tempfile.TemporaryDirectory() as temp_dir:
        mock_missing_normal_root = Path(temp_dir)
        _write_smoke_tile_level(mock_missing_normal_root, 1, include_normal=False)
        missing_normal_tile_cache = MoonCache(
            mock_missing_normal_root,
            executor=_ImmediateExecutor(),  # type: ignore[arg-type]
            tile_settings=MoonTileSettings(activation_diameter_px=260.0, hysteresis_px=60.0, shader_sampler_limit=4, enforce_source_registration=False),
        )
        try:
            missing_normal_ephemeris = MoonEphemeris(missing_normal_tile_cache, orientation_provider=orientation_selection.provider)
            missing_normal_case = next(case for case in DEFAULT_MOON_VISUAL_SMOKE_CASES if case.expected_phase == "high-zoom-first-quarter")
            missing_normal_state = missing_normal_ephemeris.state_at(missing_normal_case.observation_time_utc, missing_normal_case.latitude_deg, missing_normal_case.longitude_deg, 0.0)
            missing_normal_tile_report = missing_normal_tile_cache.prepare_tiled_textures(
                _smoke_moon_draw_state(missing_normal_state, missing_normal_case.field_width_deg),
                missing_normal_state,
                normal_maps_active=True,
            )
            missing_normal_tile_draw_state = _smoke_moon_draw_state(missing_normal_state, missing_normal_case.field_width_deg)
            missing_normal_tile_report = _diagnostics_with_render_paths(
                missing_normal_tile_cache,
                missing_normal_tile_draw_state,
                missing_normal_tile_report,
                normal_maps_active=True,
            ).as_dict()
        finally:
            missing_normal_tile_cache.shutdown()

    return {
        "scenario_count": len(scenario_reports),
        "orientation_provider_mode": orientation_selection.diagnostics.requested_mode,
        "orientation_provider_active": orientation_selection.diagnostics.active_provider,
        "spice_available": orientation_selection.diagnostics.spice_available,
        "kernel_count": len(orientation_selection.diagnostics.spice_kernel_paths),
        "fallback_reason": orientation_selection.diagnostics.fallback_reason,
        "comparison_summary": comparison_summary,
        "orientation_provider": orientation_selection.diagnostics.as_dict(),
        "orientation_comparison": orientation_comparison,
        "scenarios": scenario_reports,
        "fast_date_scrub": scrub_reports,
        "rapid_zoom_tiled_threshold": rapid_zoom_reports,
        "missing_normal_map_fallback": missing_normal_report,
        "missing_moon_tile_fallback": missing_tile_report,
        "mock_tiled_shader": mock_tiled_shader_report,
        "mock_missing_normal_tile_fallback": missing_normal_tile_report,
        "moon_focus_zoom_sequence": moon_focus_zoom_sequence,
        "moon_phase5d_overlay_alignment": moon_phase5d_overlay_alignment,
        "moon_phase5d_debug_modes": moon_phase5d_debug_modes,
        "moon_phase5e_debug_controls": moon_phase5e_debug_controls,
        "moon_phase5f_high_zoom_source": moon_phase5f_high_zoom_source,
        "moon_phase5g_real_tile_assets": moon_phase5g_real_tile_assets,
        "moon_phase5j_runtime_transition": moon_phase5j_runtime_transition,
        "moon_phase5k_normal_continuity": moon_phase5k_normal_continuity,
        "moon_phase5l_polar_guard": moon_phase5l_polar_guard,
        "moon_phase5m_polar_cap": moon_phase5m_polar_cap,
        "moon_phase5n_polar_cap_blend": moon_phase5n_polar_cap_blend,
        "moon_phase5o_polar_cap_integration": moon_phase5o_polar_cap_integration,
        "moon_tone_settings": moon_tone_settings,
        "moon_tile_diagnostics": cache.moon_tile_diagnostics.with_render_path_state(
            global_albedo_active=True,
            global_normal_active=cache.best_available_normal_texture_lod(1024.0) is not None,
            tile_bindings=cache.moon_tile_shader_bindings(normal_maps_active=True),
        ).as_dict(),
        "all_finite": all(item["finite_state"] for item in scenario_reports)
        and all(item["finite_state"] for item in scrub_reports)
        and bool(moon_focus_zoom_sequence["finite"])
        and bool(moon_focus_zoom_sequence["width_monotonic_decrease"])
        and bool(moon_focus_zoom_sequence["height_monotonic_decrease"])
        and bool(moon_focus_zoom_sequence["aspect_ratio_stable"])
        and bool(moon_focus_zoom_sequence["no_reset_to_normal_floor"])
        and bool(moon_focus_zoom_sequence["drift_width_monotonic_decrease"])
        and bool(moon_focus_zoom_sequence["drift_height_monotonic_decrease"])
        and bool(moon_focus_zoom_sequence["drift_aspect_ratio_stable"])
        and bool(moon_focus_zoom_sequence["drift_no_reset_to_normal_floor"])
        and bool(moon_phase5d_overlay_alignment["marker_hidden_due_to_extended_body"])
        and bool(moon_phase5e_debug_controls["has_expected_render_modes"])
        and bool(moon_phase5e_debug_controls["tile_isolation_request_preserved"])
        and bool(moon_phase5e_debug_controls["tone_mapping_toggle_preserved"])
        and bool(moon_phase5e_debug_controls["mutual_exclusion_state"]["force_tiled_texture_only"])
        and bool(moon_phase5f_high_zoom_source["finite"])
        and bool(moon_phase5f_high_zoom_source["moon_focused_for_background_suppression"])
        and float(moon_phase5f_high_zoom_source["background_suppression_scalar"]) > 0.0
        and bool(moon_phase5f_high_zoom_source["high_zoom_global_texture_warning"])
        and (
            not bool(moon_phase5g_real_tile_assets["real_tile_assets_present"])
            or (
                bool(moon_phase5g_real_tile_assets["real_tile_validation"]["manager_compatibility"]["usable"])
                and bool(moon_phase5g_real_tile_assets["real_tiled_shader_active"])
                and not bool(moon_phase5g_real_tile_assets["high_zoom_global_texture_warning"])
                and bool(moon_phase5g_real_tile_assets["selected_real_tile_keys"])
                and str(moon_phase5g_real_tile_assets["tile_uv_validation_status"]) == "pass"
                and str(moon_phase5g_real_tile_assets["tile_lod_luminance_consistency"].get("status")) == "pass"
                and float(moon_phase5g_real_tile_assets["visible_coverage_estimate"]) >= 0.75
            )
        )
        and bool(moon_phase5f_high_zoom_source["lighting_diagnostics"]["non_uniform_white"])
        and bool(moon_phase5j_runtime_transition["transition_safe"])
        and bool(moon_phase5k_normal_continuity["default_transition_suppresses_tiled_normal"])
        and bool(moon_phase5l_polar_guard["seam_wrap_active"])
        and bool(moon_phase5m_polar_cap["fallback_is_safe"])
        and bool(moon_phase5n_polar_cap_blend["boundary_match_bounded"])
        and bool(moon_phase5n_polar_cap_blend["normal_blend_is_conservative"])
        and bool(moon_phase5n_polar_cap_blend["ring_risk_clear"])
        and bool(moon_phase5o_polar_cap_integration["boundary_contrast_bounded"])
        and bool(moon_phase5o_polar_cap_integration["core_detail_is_integrated"])
        and bool(moon_phase5o_polar_cap_integration["footprint_risk_clear"]),
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Configure Moon visual smoke scenarios and write finite-state diagnostics.")
    parser.add_argument("--project-root", default=str(Path(__file__).resolve().parents[1]))
    parser.add_argument("--output", default="_tmp_moon_visual_smoke_result.json")
    args = parser.parse_args()

    project_root = Path(args.project_root).resolve()
    report = build_moon_visual_smoke_report(project_root)
    output_path = Path(args.output)
    if not output_path.is_absolute():
        output_path = project_root / output_path
    output_path.write_text(json.dumps(report, indent=2), encoding="utf-8")
    print(output_path)
    return 0 if report.get("all_finite") else 1


if __name__ == "__main__":
    raise SystemExit(main())
