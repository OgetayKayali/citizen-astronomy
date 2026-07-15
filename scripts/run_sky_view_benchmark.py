from __future__ import annotations

import argparse
import csv
import json
import os
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
import sys
from time import perf_counter
from typing import Any, Callable


PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

BASE_OBSERVATION_TIME = datetime(2025, 1, 14, 22, 0, tzinfo=UTC)


@dataclass(frozen=True, slots=True)
class SkyViewBenchmarkScenario:
    name: str
    description: str
    configure: Callable[["SkyViewBenchmarkHarness"], None]
    step: Callable[["SkyViewBenchmarkHarness", int, str], None] | None = None


class SkyViewBenchmarkHarness:
    def __init__(self, app: Any, canvas: Any, recorder: Any, *, width: int, height: int, qpoint_type: Any, sky_objects: tuple[Any, ...] = ()) -> None:
        self.app = app
        self.canvas = canvas
        self.recorder = recorder
        self.width = max(320, int(width))
        self.height = max(240, int(height))
        self.qpoint_type = qpoint_type
        self.sky_objects = tuple(sky_objects)
        self.failures: list[str] = []

    def process_events(self, passes: int = 2) -> None:
        for _index in range(max(1, int(passes))):
            self.app.processEvents()

    def configure_base(self) -> None:
        canvas = self.canvas
        canvas._drag_origin = None
        canvas._drag_center_anchor = None
        canvas._drag_has_moved = False
        canvas._ground_layer_interaction_defer_active = False
        canvas._camera_animation_timer.stop()
        canvas.set_simulation_frame_time(BASE_OBSERVATION_TIME, time_rate=1.0, playing=False)
        canvas.set_show_ground(False)
        canvas.set_show_equatorial_grid(False)
        canvas.set_show_alt_az_grid(False)
        canvas.set_show_constellation_lines(False)
        canvas.set_constellation_line_segments_visible(True)
        canvas.set_constellation_labels_visible(True)
        canvas.set_milky_way_enabled(True)
        canvas.set_milky_way_renderer_mode("opengl")
        canvas.set_milky_way_render_mode("advanced")
        canvas.set_milky_way_resolution_mode("high")
        canvas.set_milky_way_opacity(1.0)

    def set_camera_radec(self, ra_deg: float, dec_deg: float, field_width_deg: float) -> None:
        self.canvas._set_camera_target(float(ra_deg), float(dec_deg), float(field_width_deg), animate=False)
        self.canvas._sync_camera_targets_to_current()
        self.canvas.update()

    def set_camera_horizon(self, azimuth_deg: float, altitude_deg: float, field_width_deg: float) -> None:
        forward = self.canvas._horizon_vector_from_az_alt(float(azimuth_deg), float(altitude_deg))
        self.canvas._set_camera_orientation_state(forward, self.canvas._camera_up_for_forward(forward), float(field_width_deg))
        self.canvas._sync_camera_targets_to_current()
        self.canvas.update()

    def center_on_visible_moon(self, field_width_deg: float) -> None:
        selected_time = BASE_OBSERVATION_TIME
        selected_state = None
        for hour_offset in range(0, 96, 2):
            candidate_time = BASE_OBSERVATION_TIME + timedelta(hours=hour_offset)
            candidate_state = self.canvas._moon_ephemeris.state_at(
                candidate_time,
                self.canvas._observer_latitude_deg,
                self.canvas._observer_longitude_deg,
                self.canvas._observer_elevation_m,
            )
            if selected_state is None or candidate_state.altitude_deg > selected_state.altitude_deg:
                selected_time = candidate_time
                selected_state = candidate_state
            if candidate_state.altitude_deg >= 22.0:
                selected_time = candidate_time
                selected_state = candidate_state
                break
        if selected_state is None:
            return
        self.canvas.set_simulation_frame_time(selected_time, time_rate=1.0, playing=False)
        self.set_camera_horizon(selected_state.azimuth_deg, selected_state.altitude_deg, field_width_deg)

    def render_frame(self, *, phase: str, frame_index: int, scenario: SkyViewBenchmarkScenario) -> None:
        self.canvas._sky_view_benchmark_phase = "action"
        self.canvas._sky_view_benchmark_frame_index = frame_index
        action_token = self.recorder.start_section(
            "scenario.action",
            metadata={"scenario": scenario.name, "phase": phase, "frame_index": frame_index},
        )
        if scenario.step is not None:
            scenario.step(self, frame_index, phase)
        self.recorder.stop_section(action_token)
        self.process_events(1)

        self.canvas._sky_view_benchmark_phase = phase
        self.canvas._sky_view_benchmark_frame_index = frame_index
        frame_count_before = len(self.recorder.frame_records())
        repaint_token = self.recorder.start_section(
            "scenario.repaint_wall",
            metadata={"scenario": scenario.name, "phase": phase, "frame_index": frame_index},
        )
        repaint_start = perf_counter()
        self.canvas.repaint()
        self.process_events(1)
        self.canvas._sky_view_benchmark_phase = "post_frame"
        self.process_events(1)
        repaint_seconds = perf_counter() - repaint_start
        frame_count_after = len(self.recorder.frame_records())
        self.recorder.stop_section(
            repaint_token,
            metadata={"wall_seconds": repaint_seconds, "paint_frames_recorded": frame_count_after - frame_count_before},
        )

    def run_scenario(self, scenario: SkyViewBenchmarkScenario, *, warmup_frames: int, measured_frames: int) -> None:
        self.recorder.set_scenario(scenario.name, metadata={"description": scenario.description})
        configure_token = self.recorder.start_section("scenario.configure", metadata={"scenario": scenario.name})
        self.canvas._sky_view_benchmark_phase = "configure"
        self.canvas._sky_view_benchmark_frame_index = None
        scenario.configure(self)
        self.process_events(4)
        self.recorder.stop_section(configure_token)

        frame_count_before = len(self.recorder.frame_records())
        for frame_index in range(max(0, int(warmup_frames))):
            self.render_frame(phase="warmup", frame_index=frame_index, scenario=scenario)
        for frame_index in range(max(0, int(measured_frames))):
            self.render_frame(phase="measured", frame_index=frame_index, scenario=scenario)
        frame_count_after = len(self.recorder.frame_records())
        recorded = frame_count_after - frame_count_before
        expected = max(0, int(warmup_frames)) + max(0, int(measured_frames))
        self.recorder.mark_event("scenario.complete", metadata={"recorded_frames": recorded, "expected_frames": expected})
        if recorded <= 0:
            self.failures.append(f"{scenario.name}: no paintGL frames were recorded")


def _configure_startup_load(harness: SkyViewBenchmarkHarness) -> None:
    harness.configure_base()
    harness.set_camera_radec(83.8, -5.4, 95.0)


def _configure_idle_wide(harness: SkyViewBenchmarkHarness) -> None:
    harness.configure_base()
    harness.set_camera_radec(83.8, -5.4, 95.0)


def _configure_dense_milky_way(harness: SkyViewBenchmarkHarness) -> None:
    harness.configure_base()
    harness.set_camera_radec(266.4, -29.0, 42.0)


def _configure_dense_star_field(harness: SkyViewBenchmarkHarness) -> None:
    harness.configure_base()
    harness.set_camera_radec(83.8, -5.4, 24.0)


def _configure_horizon_ground(harness: SkyViewBenchmarkHarness) -> None:
    harness.configure_base()
    harness.canvas.set_show_ground(True)
    harness.canvas.set_show_alt_az_grid(True)
    harness.set_camera_horizon(185.0, 3.0, 92.0)


def _configure_zenith_pole(harness: SkyViewBenchmarkHarness) -> None:
    harness.configure_base()
    harness.canvas.set_show_equatorial_grid(True)
    harness.set_camera_radec(0.0, 88.5, 34.0)


def _configure_constellations(harness: SkyViewBenchmarkHarness) -> None:
    harness.configure_base()
    harness.canvas.set_show_constellation_lines(True)
    harness.canvas.set_constellation_line_segments_visible(True)
    harness.canvas.set_constellation_labels_visible(True)
    harness.set_camera_radec(83.8, 10.0, 100.0)


def _configure_moon_visible(harness: SkyViewBenchmarkHarness) -> None:
    harness.configure_base()
    harness.center_on_visible_moon(5.0)


def _configure_grid_enabled(harness: SkyViewBenchmarkHarness) -> None:
    harness.configure_base()
    harness.canvas.set_show_equatorial_grid(True)
    harness.canvas.set_show_alt_az_grid(True)
    harness.set_camera_radec(120.0, 35.0, 85.0)


def _configure_time_simulation(harness: SkyViewBenchmarkHarness) -> None:
    harness.configure_base()
    harness.canvas.set_show_equatorial_grid(True)
    harness.set_camera_radec(160.0, 20.0, 80.0)


def _step_time_simulation(harness: SkyViewBenchmarkHarness, frame_index: int, phase: str) -> None:
    del phase
    next_time = BASE_OBSERVATION_TIME + timedelta(seconds=frame_index * 75)
    harness.canvas.set_simulation_frame_time(next_time, time_rate=3600.0, playing=True)


def _configure_pan_drag(harness: SkyViewBenchmarkHarness) -> None:
    harness.configure_base()
    harness.set_camera_radec(266.4, -29.0, 50.0)
    harness.canvas._drag_origin = harness.qpoint_type(harness.width // 2, harness.height // 2)
    harness.canvas._camera_drag_right = harness.canvas._camera_right
    harness.canvas._drag_has_moved = True


def _step_pan_drag(harness: SkyViewBenchmarkHarness, frame_index: int, phase: str) -> None:
    del phase
    direction = -1.0 if frame_index % 2 else 1.0
    harness.canvas._apply_drag_rotation(5.0 * direction, 1.5, harness.canvas._view_rect())
    harness.canvas._drag_origin = harness.qpoint_type(harness.width // 2, harness.height // 2)


def _configure_zoom(harness: SkyViewBenchmarkHarness) -> None:
    harness.configure_base()
    harness.set_camera_radec(266.4, -29.0, 90.0)
    harness.canvas._ground_layer_interaction_defer_active = True


def _step_zoom(harness: SkyViewBenchmarkHarness, frame_index: int, phase: str) -> None:
    del phase
    cycle = frame_index % 80
    fraction = cycle / 79.0 if cycle else 0.0
    if cycle >= 40:
        fraction = 1.0 - ((cycle - 40) / 39.0)
    field_width = 20.0 + 90.0 * max(0.0, min(1.0, fraction))
    harness.set_camera_radec(harness.canvas._center_ra_deg, harness.canvas._center_dec_deg, field_width)


def _configure_stress_max_layers(harness: SkyViewBenchmarkHarness) -> None:
    harness.configure_base()
    harness.canvas.set_show_ground(True)
    harness.canvas.set_show_equatorial_grid(True)
    harness.canvas.set_show_alt_az_grid(True)
    harness.canvas.set_show_constellation_lines(True)
    harness.canvas.set_constellation_labels_visible(True)
    harness.center_on_visible_moon(14.0)


def _configure_minimal_layers(harness: SkyViewBenchmarkHarness) -> None:
    harness.configure_base()
    harness.canvas.set_milky_way_enabled(False)
    harness.canvas.set_show_ground(False)
    harness.canvas.set_show_equatorial_grid(False)
    harness.canvas.set_show_alt_az_grid(False)
    harness.canvas.set_show_constellation_lines(False)
    harness.set_camera_radec(83.8, -5.4, 80.0)


def _configure_layer_matrix_case(
    harness: SkyViewBenchmarkHarness,
    *,
    include_stars: bool,
    milky_way: bool,
    grid: bool,
    constellation_lines: bool,
    constellation_labels: bool,
    moon: bool,
    ground_horizon: bool,
) -> None:
    harness.configure_base()
    harness.canvas.set_objects(harness.sky_objects if include_stars else ())
    harness.center_on_visible_moon(85.0)
    harness.canvas._sky_view_moon_disabled_for_session = not moon
    harness.canvas.set_milky_way_enabled(milky_way)
    harness.canvas.set_show_equatorial_grid(grid)
    harness.canvas.set_show_alt_az_grid(grid or ground_horizon)
    harness.canvas.set_show_ground(ground_horizon)
    harness.canvas.set_show_constellation_lines(constellation_lines or constellation_labels)
    harness.canvas.set_constellation_line_segments_visible(constellation_lines)
    harness.canvas.set_constellation_labels_visible(constellation_labels)
    harness.canvas._sky_view_benchmark_layer_matrix_case = {
        "stars": include_stars,
        "milky_way": milky_way,
        "grid": grid,
        "constellation_lines": constellation_lines,
        "constellation_labels": constellation_labels,
        "moon": moon,
        "ground_horizon": ground_horizon,
    }


_LAYER_MATRIX_CASES: tuple[tuple[str, str, dict[str, bool]], ...] = (
    ("layer_matrix_01_minimal_baseline", "minimal baseline", dict(include_stars=False, milky_way=False, grid=False, constellation_lines=False, constellation_labels=False, moon=False, ground_horizon=False)),
    ("layer_matrix_02_stars", "+ stars", dict(include_stars=True, milky_way=False, grid=False, constellation_lines=False, constellation_labels=False, moon=False, ground_horizon=False)),
    ("layer_matrix_03_milky_way", "+ Milky Way", dict(include_stars=True, milky_way=True, grid=False, constellation_lines=False, constellation_labels=False, moon=False, ground_horizon=False)),
    ("layer_matrix_04_grid", "+ grid", dict(include_stars=True, milky_way=False, grid=True, constellation_lines=False, constellation_labels=False, moon=False, ground_horizon=False)),
    ("layer_matrix_05_constellation_lines_only", "+ constellations lines only", dict(include_stars=True, milky_way=False, grid=False, constellation_lines=True, constellation_labels=False, moon=False, ground_horizon=False)),
    ("layer_matrix_06_constellation_labels_only", "+ constellations labels only", dict(include_stars=True, milky_way=False, grid=False, constellation_lines=False, constellation_labels=True, moon=False, ground_horizon=False)),
    ("layer_matrix_07_constellation_lines_and_labels", "+ constellation lines and labels", dict(include_stars=True, milky_way=False, grid=False, constellation_lines=True, constellation_labels=True, moon=False, ground_horizon=False)),
    ("layer_matrix_08_moon", "+ Moon", dict(include_stars=True, milky_way=False, grid=False, constellation_lines=False, constellation_labels=False, moon=True, ground_horizon=False)),
    ("layer_matrix_09_ground_horizon", "+ ground/horizon", dict(include_stars=True, milky_way=False, grid=False, constellation_lines=False, constellation_labels=False, moon=False, ground_horizon=True)),
    ("layer_matrix_10_all_normal_layers", "+ all normal layers", dict(include_stars=True, milky_way=True, grid=True, constellation_lines=True, constellation_labels=True, moon=True, ground_horizon=True)),
)


def _build_layer_matrix_scenarios() -> tuple[SkyViewBenchmarkScenario, ...]:
    scenarios: list[SkyViewBenchmarkScenario] = []
    for name, label, options in _LAYER_MATRIX_CASES:
        def configure(harness: SkyViewBenchmarkHarness, resolved_options: dict[str, bool] = options) -> None:
            _configure_layer_matrix_case(harness, **resolved_options)

        scenarios.append(SkyViewBenchmarkScenario(name, f"Layer toggle matrix: {label}.", configure))
    return tuple(scenarios)


def _build_scenarios() -> tuple[SkyViewBenchmarkScenario, ...]:
    return (
        SkyViewBenchmarkScenario("startup_load", "Initial widget, catalog, and first-frame load path.", _configure_startup_load),
        SkyViewBenchmarkScenario("idle_wide_fov", "Stable wide field with default expensive layers available.", _configure_idle_wide),
        SkyViewBenchmarkScenario("dense_milky_way", "Galactic-center view with Advanced Milky Way tiles active.", _configure_dense_milky_way),
        SkyViewBenchmarkScenario("dense_star_field", "Narrow Orion-region star/object projection path.", _configure_dense_star_field),
        SkyViewBenchmarkScenario("horizon_ground_heavy", "Low-altitude view with ground and alt/az grid active.", _configure_horizon_ground),
        SkyViewBenchmarkScenario("zenith_pole_like", "Near-pole/zenith-style camera orientation with grid enabled.", _configure_zenith_pole),
        SkyViewBenchmarkScenario("constellations_enabled", "Constellation line and label projection/draw path.", _configure_constellations),
        SkyViewBenchmarkScenario("moon_visible", "Moon visible and centered enough to exercise Moon texture/render path.", _configure_moon_visible),
        SkyViewBenchmarkScenario("grid_enabled", "Equatorial and alt/az grid rendering together.", _configure_grid_enabled),
        SkyViewBenchmarkScenario("time_simulation_enabled", "Time updates before each frame with grid invalidation active.", _configure_time_simulation, _step_time_simulation),
        SkyViewBenchmarkScenario("pan_drag_simulation", "Synthetic drag camera response with interaction state marked as drag.", _configure_pan_drag, _step_pan_drag),
        SkyViewBenchmarkScenario("zoom_in_out_simulation", "Synthetic wheel-like zoom response across wide and narrow FOVs.", _configure_zoom, _step_zoom),
        SkyViewBenchmarkScenario("stress_max_layers", "Ground, grids, constellations, Milky Way, and Moon together.", _configure_stress_max_layers),
        SkyViewBenchmarkScenario("minimal_layers_disabled", "Milky Way, grids, ground, and constellations disabled.", _configure_minimal_layers),
    )


def _write_json(output_dir: Path, payload: dict[str, Any]) -> Path:
    path = output_dir / "benchmark_results.json"
    path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    return path


def _write_section_csv(output_dir: Path, payload: dict[str, Any]) -> Path:
    path = output_dir / "benchmark_results.csv"
    fields = (
        "scenario",
        "frame_phase",
        "path",
        "count",
        "total_milliseconds",
        "avg_milliseconds",
        "median_milliseconds",
        "min_milliseconds",
        "max_milliseconds",
        "p95_milliseconds",
        "last_milliseconds",
    )
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        for row in payload.get("section_summaries", []):
            writer.writerow({field: row.get(field, "") for field in fields})
    return path


def _write_frame_csv(output_dir: Path, payload: dict[str, Any]) -> Path:
    path = output_dir / "frame_times.csv"
    fields = (
        "scenario",
        "frame_phase",
        "frame_index",
        "frame_number",
        "total_milliseconds",
        "interaction_state",
        "visible_objects",
        "visible_stars",
        "show_ground",
        "show_equatorial_grid",
        "show_alt_az_grid",
        "show_constellation_lines",
        "milky_way_enabled",
        "milky_way_renderer",
        "milky_way_visible_tiles",
        "milky_way_requested_tiles",
        "milky_way_resident_tiles",
        "milky_way_drawn_tiles",
        "milky_way_draw_calls",
        "milky_way_selected_lod",
        "milky_way_base_drawn",
        "milky_way_support_mask_used",
        "milky_way_fallback_used",
        "constellation_segments_considered",
        "constellation_segments_drawn",
        "constellation_labels_considered",
        "constellation_labels_drawn",
        "constellation_geometry_cache_status",
        "constellation_label_cache_hits",
        "constellation_label_cache_misses",
        "grid_type_enabled",
        "grid_lines_generated",
        "grid_vertices_projected",
        "grid_geometry_cache_status",
        "moon_visible",
        "moon_angular_size_deg",
        "moon_diameter_px",
        "moon_texture_route",
        "moon_tiles_drawn",
        "moon_polar_cap_textures",
        "moon_texture_binds",
        "moon_draw_calls",
        "moon_failure_reason",
        "gc_ran",
        "gc_collections_delta",
        "python_tracked_objects_after",
    )
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        for frame in payload.get("frame_records", []):
            metadata = frame.get("metadata", {}) if isinstance(frame.get("metadata"), dict) else {}
            writer.writerow(
                {
                    "scenario": frame.get("scenario", ""),
                    "frame_phase": frame.get("frame_phase", ""),
                    "frame_index": frame.get("frame_index", ""),
                    "frame_number": frame.get("frame_number", ""),
                    "total_milliseconds": frame.get("total_milliseconds", ""),
                    "interaction_state": frame.get("interaction_state", ""),
                    "visible_objects": metadata.get("visible_objects", ""),
                    "visible_stars": metadata.get("visible_stars", ""),
                    "show_ground": metadata.get("show_ground", ""),
                    "show_equatorial_grid": metadata.get("show_equatorial_grid", ""),
                    "show_alt_az_grid": metadata.get("show_alt_az_grid", ""),
                    "show_constellation_lines": metadata.get("show_constellation_lines", ""),
                    "milky_way_enabled": metadata.get("milky_way_enabled", ""),
                    "milky_way_renderer": metadata.get("milky_way_renderer", ""),
                    "milky_way_visible_tiles": metadata.get("milky_way_visible_tiles", ""),
                    "milky_way_requested_tiles": metadata.get("milky_way_requested_tiles", ""),
                    "milky_way_resident_tiles": metadata.get("milky_way_resident_tiles", ""),
                    "milky_way_drawn_tiles": metadata.get("milky_way_drawn_tiles", ""),
                    "milky_way_draw_calls": metadata.get("milky_way_draw_calls", ""),
                    "milky_way_selected_lod": metadata.get("milky_way_selected_lod", ""),
                    "milky_way_base_drawn": metadata.get("milky_way_base_drawn", ""),
                    "milky_way_support_mask_used": metadata.get("milky_way_support_mask_used", ""),
                    "milky_way_fallback_used": metadata.get("milky_way_fallback_used", ""),
                    "constellation_segments_considered": metadata.get("constellation_segments_considered", ""),
                    "constellation_segments_drawn": metadata.get("constellation_segments_drawn", ""),
                    "constellation_labels_considered": metadata.get("constellation_labels_considered", ""),
                    "constellation_labels_drawn": metadata.get("constellation_labels_drawn", ""),
                    "constellation_geometry_cache_status": metadata.get("constellation_geometry_cache_status", ""),
                    "constellation_label_cache_hits": metadata.get("constellation_label_cache_hits", ""),
                    "constellation_label_cache_misses": metadata.get("constellation_label_cache_misses", ""),
                    "grid_type_enabled": metadata.get("grid_type_enabled", ""),
                    "grid_lines_generated": metadata.get("grid_lines_generated", ""),
                    "grid_vertices_projected": metadata.get("grid_vertices_projected", ""),
                    "grid_geometry_cache_status": metadata.get("grid_geometry_cache_status", ""),
                    "moon_visible": metadata.get("moon_visible", ""),
                    "moon_angular_size_deg": metadata.get("moon_angular_size_deg", ""),
                    "moon_diameter_px": metadata.get("moon_diameter_px", ""),
                    "moon_texture_route": metadata.get("moon_texture_route", ""),
                    "moon_tiles_drawn": metadata.get("moon_tiles_drawn", ""),
                    "moon_polar_cap_textures": metadata.get("moon_polar_cap_textures", ""),
                    "moon_texture_binds": metadata.get("moon_texture_binds", ""),
                    "moon_draw_calls": metadata.get("moon_draw_calls", ""),
                    "moon_failure_reason": metadata.get("moon_failure_reason", ""),
                    "gc_ran": metadata.get("gc_ran", ""),
                    "gc_collections_delta": metadata.get("gc_collections_delta", ""),
                    "python_tracked_objects_after": metadata.get("python_tracked_objects_after", ""),
                }
            )
    return path


def _measured_rows(payload: dict[str, Any], key: str) -> list[dict[str, Any]]:
    return [row for row in payload.get(key, []) if row.get("frame_phase") == "measured" or key == "frame_summaries"]


def _format_ms(value: object) -> str:
    try:
        return f"{float(value):.3f}"
    except (TypeError, ValueError):
        return "0.000"


def _top_section_rows(payload: dict[str, Any], *, limit: int = 20) -> list[dict[str, Any]]:
    rows = [row for row in payload.get("section_summaries", []) if row.get("frame_phase") == "measured"]
    rows.sort(key=lambda item: float(item.get("total_milliseconds") or 0.0), reverse=True)
    return rows[:limit]


def _percentile_milliseconds(values: list[float], percentile: float) -> float:
    if not values:
        return 0.0
    if len(values) == 1:
        return values[0]
    ordered = sorted(values)
    rank = (len(ordered) - 1) * percentile
    lower = int(rank)
    upper = min(lower + 1, len(ordered) - 1)
    fraction = rank - lower
    return ordered[lower] * (1.0 - fraction) + ordered[upper] * fraction


def _frame_phase_rows(payload: dict[str, Any], phase: str, *, limit: int | None = None) -> list[dict[str, Any]]:
    grouped: dict[str, list[float]] = {}
    for frame in payload.get("frame_records", []):
        if frame.get("frame_phase") != phase:
            continue
        scenario = str(frame.get("scenario", ""))
        grouped.setdefault(scenario, []).append(float(frame.get("total_milliseconds") or 0.0))
    rows: list[dict[str, Any]] = []
    for scenario, values in grouped.items():
        if not values:
            continue
        ordered = sorted(values)
        middle = len(ordered) // 2
        if len(ordered) % 2:
            median = ordered[middle]
        else:
            median = (ordered[middle - 1] + ordered[middle]) / 2.0
        rows.append(
            {
                "scenario": scenario,
                "count": len(values),
                "avg_milliseconds": sum(values) / len(values),
                "median_milliseconds": median,
                "p95_milliseconds": _percentile_milliseconds(values, 0.95),
                "max_milliseconds": max(values),
            }
        )
    rows.sort(key=lambda item: float(item.get("avg_milliseconds") or 0.0), reverse=True)
    if limit is not None:
        return rows[:limit]
    return rows


def _startup_section_rows(payload: dict[str, Any]) -> list[dict[str, Any]]:
    rows = [
        row
        for row in payload.get("section_summaries", [])
        if row.get("scenario") == "startup_load"
        and row.get("frame_phase") == "manual"
        and str(row.get("path", "")).startswith("startup.")
    ]
    rows.sort(key=lambda item: float(item.get("total_milliseconds") or 0.0), reverse=True)
    return rows


def _unexpected_work_rows(payload: dict[str, Any]) -> list[tuple[str, str, int, float]]:
    suspicious_terms = ("file_read", "decode", "upload", "program_init")
    counts: dict[tuple[str, str], tuple[int, float]] = {}
    for sample in payload.get("section_samples", []):
        if sample.get("frame_phase") != "measured":
            continue
        path = str(sample.get("path", ""))
        if not any(term in path for term in suspicious_terms):
            continue
        key = (str(sample.get("scenario", "")), path)
        count, total_ms = counts.get(key, (0, 0.0))
        counts[key] = (count + 1, total_ms + float(sample.get("milliseconds") or 0.0))
    rows = [(scenario, path, count, total_ms) for (scenario, path), (count, total_ms) in counts.items()]
    rows.sort(key=lambda item: item[3], reverse=True)
    return rows[:12]


def _measured_frame_values(payload: dict[str, Any], scenario: str) -> list[float]:
    return [
        float(frame.get("total_milliseconds") or 0.0)
        for frame in payload.get("frame_records", [])
        if frame.get("scenario") == scenario and frame.get("frame_phase") == "measured"
    ]


def _summarize_milliseconds(values: list[float]) -> dict[str, float | int]:
    if not values:
        return {"count": 0, "avg_milliseconds": 0.0, "median_milliseconds": 0.0, "p95_milliseconds": 0.0, "max_milliseconds": 0.0}
    ordered = sorted(values)
    return {
        "count": len(values),
        "avg_milliseconds": sum(values) / len(values),
        "median_milliseconds": _percentile_milliseconds(values, 0.50),
        "p95_milliseconds": _percentile_milliseconds(values, 0.95),
        "max_milliseconds": ordered[-1],
    }


def _layer_matrix_rows(payload: dict[str, Any]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    baseline_avg = 0.0
    previous_avg = 0.0
    for index, (scenario_name, label, _options) in enumerate(_LAYER_MATRIX_CASES):
        summary = _summarize_milliseconds(_measured_frame_values(payload, scenario_name))
        avg_ms = float(summary.get("avg_milliseconds") or 0.0)
        if index == 0:
            baseline_avg = avg_ms
        rows.append(
            {
                "order": index + 1,
                "case": label,
                "scenario": scenario_name,
                **summary,
                "delta_vs_baseline_milliseconds": avg_ms - baseline_avg,
                "delta_vs_previous_milliseconds": 0.0 if index == 0 else avg_ms - previous_avg,
            }
        )
        previous_avg = avg_ms
    return rows


def _write_layer_matrix_csv(output_dir: Path, payload: dict[str, Any]) -> Path:
    path = output_dir / "layer_toggle_matrix.csv"
    fields = (
        "order",
        "case",
        "scenario",
        "count",
        "avg_milliseconds",
        "median_milliseconds",
        "p95_milliseconds",
        "max_milliseconds",
        "delta_vs_baseline_milliseconds",
        "delta_vs_previous_milliseconds",
    )
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        for row in _layer_matrix_rows(payload):
            writer.writerow({field: row.get(field, "") for field in fields})
    return path


def _write_layer_matrix_markdown(output_dir: Path, payload: dict[str, Any]) -> Path:
    path = output_dir / "layer_toggle_matrix.md"
    lines = ["# Layer Toggle Matrix", ""]
    lines.append("All matrix cases use the same camera/FOV and isolate layers by toggling benchmark-harness state only.")
    lines.append("")
    lines.append("| # | Case | Frames | Avg ms | Median ms | P95 ms | Max ms | Delta vs baseline | Delta vs previous |")
    lines.append("| ---: | --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: |")
    for row in _layer_matrix_rows(payload):
        lines.append(
            f"| {row.get('order', '')} | {row.get('case', '')} | {row.get('count', 0)} | "
            f"{_format_ms(row.get('avg_milliseconds'))} | {_format_ms(row.get('median_milliseconds'))} | "
            f"{_format_ms(row.get('p95_milliseconds'))} | {_format_ms(row.get('max_milliseconds'))} | "
            f"{_format_ms(row.get('delta_vs_baseline_milliseconds'))} | {_format_ms(row.get('delta_vs_previous_milliseconds'))} |"
        )
    lines.append("")
    path.write_text("\n".join(lines), encoding="utf-8")
    return path


def _write_slow_frames_markdown(output_dir: Path, payload: dict[str, Any]) -> Path:
    path = output_dir / "slow_frames.md"
    lines = ["# Slow Frames", ""]
    slow_frames = payload.get("slow_frames", [])
    if not slow_frames:
        lines.append("No frames exceeded the configured slow-frame threshold.")
        path.write_text("\n".join(lines), encoding="utf-8")
        return path
    for frame in slow_frames:
        metadata = frame.get("metadata", {}) if isinstance(frame.get("metadata"), dict) else {}
        lines.append(f"## {frame.get('scenario', '')} frame {frame.get('frame_index', '')}")
        lines.append("")
        lines.append(
            f"- Total: {_format_ms(frame.get('total_milliseconds'))} ms; phase: {frame.get('frame_phase', '')}; state: {frame.get('interaction_state', '')}"
        )
        lines.append(
            f"- GC: ran={metadata.get('gc_ran', '')}; collections={metadata.get('gc_collections_delta', '')}; tracked_objects={metadata.get('python_tracked_objects_after', '')}"
        )
        lines.append(
            f"- Milky Way: renderer={metadata.get('milky_way_renderer', '')}; tiles={metadata.get('milky_way_drawn_tiles', '')}/{metadata.get('milky_way_requested_tiles', '')}; draw_calls={metadata.get('milky_way_draw_calls', '')}"
        )
        lines.append(
            f"- Moon: visible={metadata.get('moon_visible', '')}; route={metadata.get('moon_texture_route', '')}; tiles={metadata.get('moon_tiles_drawn', '')}; binds={metadata.get('moon_texture_binds', '')}"
        )
        top_sections = frame.get("top_sections", []) if isinstance(frame.get("top_sections"), list) else []
        if top_sections:
            lines.append("")
            lines.append("| Section | ms |")
            lines.append("| --- | ---: |")
            for section in top_sections[:20]:
                lines.append(f"| {section.get('path', '')} | {_format_ms(section.get('milliseconds'))} |")
        recent_events = frame.get("recent_events", []) if isinstance(frame.get("recent_events"), list) else []
        if recent_events:
            lines.append("")
            lines.append("Recent events: " + ", ".join(str(event.get("name", "")) for event in recent_events[-8:]))
        lines.append("")
    path.write_text("\n".join(lines), encoding="utf-8")
    return path


def _write_gc_diagnostic_markdown(output_dir: Path, payload: dict[str, Any]) -> Path:
    path = output_dir / "gc_diagnostic.md"
    measured_frames = [frame for frame in payload.get("frame_records", []) if frame.get("frame_phase") == "measured"]
    gc_frames = []
    for frame in measured_frames:
        metadata = frame.get("metadata", {}) if isinstance(frame.get("metadata"), dict) else {}
        if metadata.get("gc_ran"):
            gc_frames.append(frame)
    lines = ["# GC Diagnostic", ""]
    lines.append("This diagnostic reports normal benchmark GC metadata only; it does not disable GC or alter rendering behavior.")
    lines.append("")
    lines.append(f"- Measured frames: {len(measured_frames)}")
    lines.append(f"- Frames with GC collection delta: {len(gc_frames)}")
    if gc_frames:
        gc_frames.sort(key=lambda frame: float(frame.get("total_milliseconds") or 0.0), reverse=True)
        lines.append("")
        lines.append("| Scenario | Frame | Total ms | GC delta |")
        lines.append("| --- | ---: | ---: | --- |")
        for frame in gc_frames[:20]:
            metadata = frame.get("metadata", {}) if isinstance(frame.get("metadata"), dict) else {}
            lines.append(
                f"| {frame.get('scenario', '')} | {frame.get('frame_index', '')} | {_format_ms(frame.get('total_milliseconds'))} | {metadata.get('gc_collections_delta', '')} |"
            )
    lines.append("")
    path.write_text("\n".join(lines), encoding="utf-8")
    return path


def _write_summary_markdown(output_dir: Path, payload: dict[str, Any], *, failures: list[str]) -> Path:
    path = output_dir / "benchmark_summary.md"
    lines: list[str] = []
    lines.append("# Sky View Benchmark Summary")
    lines.append("")
    lines.append(f"Generated: {payload.get('generated_at_utc', 'unknown')}")
    lines.append("")
    lines.append("GPU-facing timings are CPU-side proxy measurements. This run does not use OpenGL timer queries or glFinish synchronization, so draw/upload sections measure CPU submission and Qt/OpenGL call cost rather than complete GPU execution time.")
    lines.append("")
    lines.append("## Environment")
    environment = payload.get("environment", {}) if isinstance(payload.get("environment"), dict) else {}
    lines.append(f"- Platform: {environment.get('platform', 'unknown')}")
    lines.append(f"- Python: {environment.get('python_version', 'unknown')} ({environment.get('python_implementation', 'unknown')})")
    lines.append(f"- CPU count: {environment.get('cpu_count', 'unknown')}")
    lines.append(f"- Benchmark env: {environment.get('benchmark_env_value', 'unknown')}")
    lines.append("")
    if failures:
        lines.append("## Failures")
        for failure in failures:
            lines.append(f"- {failure}")
        lines.append("")
    lines.append("## Scenario Frame Costs")
    lines.append("| Scenario | Frames | Avg ms | Median ms | P95 ms | Max ms |")
    lines.append("| --- | ---: | ---: | ---: | ---: | ---: |")
    for row in sorted(payload.get("frame_summaries", []), key=lambda item: str(item.get("scenario", ""))):
        lines.append(
            "| "
            + str(row.get("scenario", ""))
            + " | "
            + str(row.get("count", 0))
            + " | "
            + _format_ms(row.get("avg_milliseconds"))
            + " | "
            + _format_ms(row.get("median_milliseconds"))
            + " | "
            + _format_ms(row.get("p95_milliseconds"))
            + " | "
            + _format_ms(row.get("max_milliseconds"))
            + " |"
        )
    lines.append("")
    startup_rows = _startup_section_rows(payload)
    if startup_rows:
        lines.append("## Startup And Loading Sections")
        lines.append("| Section | Total ms | Avg ms | Count |")
        lines.append("| --- | ---: | ---: | ---: |")
        for row in startup_rows[:12]:
            lines.append(
                f"| {row.get('path', '')} | {_format_ms(row.get('total_milliseconds'))} | "
                f"{_format_ms(row.get('avg_milliseconds'))} | {row.get('count', 0)} |"
            )
        lines.append("")
    response_rows = _frame_phase_rows(payload, "action", limit=12)
    if response_rows:
        lines.append("## Response / Action Paint Costs")
        lines.append("| Scenario | Action Paints | Avg ms | Median ms | P95 ms | Max ms |")
        lines.append("| --- | ---: | ---: | ---: | ---: | ---: |")
        for row in response_rows:
            lines.append(
                "| "
                + str(row.get("scenario", ""))
                + " | "
                + str(row.get("count", 0))
                + " | "
                + _format_ms(row.get("avg_milliseconds"))
                + " | "
                + _format_ms(row.get("median_milliseconds"))
                + " | "
                + _format_ms(row.get("p95_milliseconds"))
                + " | "
                + _format_ms(row.get("max_milliseconds"))
                + " |"
            )
        lines.append("")
    lines.append("## Largest Measured Sections")
    lines.append("| Scenario | Section | Count | Total ms | Avg ms | P95 ms | Max ms |")
    lines.append("| --- | --- | ---: | ---: | ---: | ---: | ---: |")
    for row in _top_section_rows(payload, limit=20):
        lines.append(
            f"| {row.get('scenario', '')} | {row.get('path', '')} | {row.get('count', 0)} | "
            f"{_format_ms(row.get('total_milliseconds'))} | {_format_ms(row.get('avg_milliseconds'))} | "
            f"{_format_ms(row.get('p95_milliseconds'))} | {_format_ms(row.get('max_milliseconds'))} |"
        )
    lines.append("")
    lines.append("## Refined Bottleneck Questions")
    lines.append("- Startup/loading: see startup sections and any measured-frame read/decode/upload rows below.")
    lines.append("- Response time: see action-paint costs and `scenario.repaint_wall` rows in the CSV/JSON.")
    lines.append("- Per-frame rendering: see largest measured sections plus `frame_times.csv` layer counters.")
    lines.append("- Milky Way/background: sections now split visibility, runtime inputs, LOD/tile selection, cache lookup, shader setup, support mask, base draw, high-res tiled draw, texture binds, and evictions.")
    lines.append("- Grid: sections now split cache lookup, coordinate generation, polyline generation, clipping, projection, path build, and drawing.")
    lines.append("- Constellations: sections now split cache lookup/rebuild, line culling/clipping/projection/batching/drawing, label projection/fade/text-cache/drawing.")
    lines.append("- Moon: sections now split visibility, route selection, draw-state projection, texture prep, texture binds, shader setup, uniforms, and draw call.")
    lines.append("- Slow frames/time simulation/GC: see `slow_frames.md`, `frame_times.csv`, event metadata, and `gc_diagnostic.md`.")
    lines.append("")
    matrix_rows = _layer_matrix_rows(payload)
    if any(int(row.get("count", 0) or 0) > 0 for row in matrix_rows):
        lines.append("## Layer Toggle Matrix")
        lines.append("| Case | Avg ms | Delta vs baseline | Delta vs previous |")
        lines.append("| --- | ---: | ---: | ---: |")
        for row in matrix_rows:
            lines.append(
                f"| {row.get('case', '')} | {_format_ms(row.get('avg_milliseconds'))} | "
                f"{_format_ms(row.get('delta_vs_baseline_milliseconds'))} | {_format_ms(row.get('delta_vs_previous_milliseconds'))} |"
            )
        lines.append("")
    lines.append("## Slow Frames")
    lines.append("| Scenario | Frame | State | Total ms | Top section | Top section ms |")
    lines.append("| --- | ---: | --- | ---: | --- | ---: |")
    for frame in payload.get("slow_frames", [])[:15]:
        top_sections = frame.get("top_sections", []) if isinstance(frame.get("top_sections"), list) else []
        top_section = top_sections[0] if top_sections else {}
        lines.append(
            f"| {frame.get('scenario', '')} | {frame.get('frame_index', '')} | {frame.get('interaction_state', '')} | "
            f"{_format_ms(frame.get('total_milliseconds'))} | {top_section.get('path', '')} | {_format_ms(top_section.get('milliseconds'))} |"
        )
    lines.append("")
    lines.append("## Unexpected Per-Frame Work")
    unexpected_rows = _unexpected_work_rows(payload)
    if not unexpected_rows:
        lines.append("No measured-frame file read, decode, upload, or program-init sections were recorded.")
    else:
        lines.append("| Scenario | Section | Count | Total ms |")
        lines.append("| --- | --- | ---: | ---: |")
        for scenario, section, count, total_ms in unexpected_rows:
            lines.append(f"| {scenario} | {section} | {count} | {total_ms:.3f} |")
    lines.append("")
    lines.append("## Next Targets, Not Implemented")
    lines.append("- Investigate any measured-frame decode/read/upload sections that remain after warmup.")
    lines.append("- Compare slow frames against frame metadata for tile residency, Moon texture readiness, grid state, and interaction state.")
    lines.append("- Add GPU timer queries only if CPU proxy timings are not enough to explain frame cost.")
    lines.append("")
    path.write_text("\n".join(lines), encoding="utf-8")
    return path


def _parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run the Citizen Astronomy Sky View benchmark scenarios.")
    parser.add_argument("--output-dir", default="benchmark_results_refined")
    parser.add_argument("--warmup-frames", type=int, default=30)
    parser.add_argument("--measured-frames", type=int, default=300)
    parser.add_argument("--width", type=int, default=1280)
    parser.add_argument("--height", type=int, default=720)
    parser.add_argument("--scenario", action="append", default=None, help="Run only the named scenario. May be provided more than once.")
    parser.add_argument("--no-section-samples", action="store_true", help="Omit raw section samples from benchmark_results.json.")
    parser.add_argument("--skip-layer-matrix", action="store_true", help="Skip the focused layer-toggle matrix scenarios.")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(sys.argv[1:] if argv is None else argv)
    os.environ["CITIZEN_PHOTOMETRY_BENCHMARK"] = "1"

    from photometry_app.core.benchmarking import reset_global_benchmark_recorder

    recorder = reset_global_benchmark_recorder(enabled=True)
    recorder.set_scenario("startup_load", metadata={"description": "Benchmark process startup and Sky View setup."})
    import_token = recorder.start_section("startup.import_modules")
    from PySide6.QtCore import QPoint
    from PySide6.QtWidgets import QApplication

    from photometry_app.core.sky_atlas import load_local_sky_atlas_objects
    from photometry_app.ui.main_window import _SkyAtlasViewWidget
    recorder.stop_section(import_token)

    output_dir = Path(args.output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    app_token = recorder.start_section("startup.qapplication")
    app = QApplication.instance() or QApplication([sys.argv[0]])
    recorder.stop_section(app_token)

    widget_token = recorder.start_section("startup.sky_view_widget_init")
    canvas = _SkyAtlasViewWidget()
    canvas._sky_view_benchmark_phase = "startup"
    canvas._sky_view_benchmark_frame_index = None
    canvas.resize(max(320, int(args.width)), max(240, int(args.height)))
    canvas.show()
    recorder.stop_section(widget_token, metadata={"width": int(args.width), "height": int(args.height)})

    harness = SkyViewBenchmarkHarness(app, canvas, recorder, width=args.width, height=args.height, qpoint_type=QPoint)
    harness.process_events(6)

    catalog_token = recorder.start_section("startup.catalog_load")
    sky_objects = load_local_sky_atlas_objects()
    recorder.stop_section(catalog_token, metadata={"objects": len(sky_objects), "source": "packaged_local"})
    harness.sky_objects = tuple(sky_objects)

    set_objects_token = recorder.start_section("startup.set_objects")
    canvas.set_objects(sky_objects)
    recorder.stop_section(set_objects_token, metadata={"objects": len(sky_objects)})
    harness.process_events(6)

    scenarios = _build_scenarios()
    if args.scenario:
        selected_names = {str(name).strip() for name in args.scenario if str(name).strip()}
        scenarios = tuple(scenario for scenario in scenarios if scenario.name in selected_names)
        missing_names = sorted(selected_names - {scenario.name for scenario in scenarios})
        if missing_names:
            harness.failures.append("Unknown scenarios: " + ", ".join(missing_names))
    for scenario in scenarios:
        harness.run_scenario(scenario, warmup_frames=args.warmup_frames, measured_frames=args.measured_frames)

    if not args.scenario and not bool(args.skip_layer_matrix):
        for scenario in _build_layer_matrix_scenarios():
            harness.run_scenario(scenario, warmup_frames=args.warmup_frames, measured_frames=args.measured_frames)

    canvas.close()
    harness.process_events(4)

    payload = recorder.to_dict(include_samples=not bool(args.no_section_samples))
    payload["command"] = [sys.executable, str(Path(__file__).resolve()), *sys.argv[1:]]
    payload["requested_warmup_frames"] = int(args.warmup_frames)
    payload["requested_measured_frames"] = int(args.measured_frames)
    payload["viewport"] = {"width": int(args.width), "height": int(args.height)}
    payload["failures"] = tuple(harness.failures)

    json_path = _write_json(output_dir, payload)
    csv_path = _write_section_csv(output_dir, payload)
    frame_csv_path = _write_frame_csv(output_dir, payload)
    slow_frames_path = _write_slow_frames_markdown(output_dir, payload)
    matrix_csv_path = _write_layer_matrix_csv(output_dir, payload)
    matrix_md_path = _write_layer_matrix_markdown(output_dir, payload)
    gc_diagnostic_path = _write_gc_diagnostic_markdown(output_dir, payload)
    summary_path = _write_summary_markdown(output_dir, payload, failures=harness.failures)

    print(f"Wrote {summary_path}")
    print(f"Wrote {json_path}")
    print(f"Wrote {csv_path}")
    print(f"Wrote {frame_csv_path}")
    print(f"Wrote {slow_frames_path}")
    print(f"Wrote {matrix_csv_path}")
    print(f"Wrote {matrix_md_path}")
    print(f"Wrote {gc_diagnostic_path}")
    if harness.failures:
        for failure in harness.failures:
            print(f"Benchmark warning: {failure}", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())