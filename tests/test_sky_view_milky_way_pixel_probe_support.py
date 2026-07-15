from __future__ import annotations

import json

import numpy as np

from photometry_app.ui.sky_view_milky_way_pixel_probe_support import (
    IncrementalJsonReport,
    build_capture_coordinate_record,
    build_default_probe_samples,
    decode_packed_uv,
    decode_packed_vec3,
    decode_tile_id_exact,
    decode_u16,
    evaluate_sampling_models,
    load_probe_samples_from_json,
    sample_model_gl_linear,
    screenshot_to_logical_xy,
    summarize_model_mae,
    top_left_to_bottom_left_readback_y,
)


def test_decode_helpers_round_trip() -> None:
    packed_uv = np.asarray((0x12, 0x34, 0xAB, 0xCD), dtype=np.uint8)
    packed_hi = np.asarray((0x11, 0x22, 0x33, 0x00), dtype=np.uint8)
    packed_lo = np.asarray((0x44, 0x55, 0x66, 0x00), dtype=np.uint8)

    assert decode_u16(0x12, 0x34) == 0x1234 / 65535.0
    assert np.allclose(decode_packed_uv(packed_uv), np.asarray((0x1234 / 65535.0, 0xABCD / 65535.0), dtype=np.float64))
    assert np.allclose(
        decode_packed_vec3(packed_hi, packed_lo),
        np.asarray((0x1144 / 65535.0, 0x2255 / 65535.0, 0x3366 / 65535.0), dtype=np.float64),
    )
    assert decode_tile_id_exact(np.asarray((4, 17, 29), dtype=np.uint8)) == (4, 17, 29)


def test_build_default_probe_samples_produces_unique_minimum_set() -> None:
    final_rgba8 = np.zeros((120, 240, 4), dtype=np.uint8)

    samples = build_default_probe_samples(final_rgba8, max_samples=24)

    assert len(samples) >= 16
    assert len(samples) <= 24
    assert len({(sample["x"], sample["y"]) for sample in samples}) == len(samples)
    assert any(sample["classification"] == "wedge_interior" for sample in samples)
    assert any(sample["classification"] == "clean_control" for sample in samples)


def test_load_probe_samples_from_json_clamps_coordinates(tmp_path) -> None:
    sample_path = tmp_path / "samples.json"
    sample_path.write_text(
        json.dumps(
            {
                "samples": [
                    {"sample_id": "manual_a", "x": -10, "y": 999, "classification": "manual"},
                    {"sample_id": "manual_b", "x": 4, "y": 7, "tags": ["manual", "control"]},
                ]
            }
        ),
        encoding="utf-8",
    )

    samples = load_probe_samples_from_json(sample_path, width=10, height=8)

    assert samples[0]["x"] == 0
    assert samples[0]["y"] == 7
    assert samples[1]["tags"] == ["manual", "control"]


def test_evaluate_sampling_models_prefers_gl_linear_for_matching_sample() -> None:
    tile_image = np.asarray(
        [
            [(0.0, 0.1, 0.2), (0.2, 0.3, 0.4), (0.4, 0.5, 0.6), (0.6, 0.7, 0.8)],
            [(0.1, 0.2, 0.3), (0.3, 0.4, 0.5), (0.5, 0.6, 0.7), (0.7, 0.8, 0.9)],
            [(0.2, 0.3, 0.4), (0.4, 0.5, 0.6), (0.6, 0.7, 0.8), (0.8, 0.9, 1.0)],
            [(0.3, 0.4, 0.5), (0.5, 0.6, 0.7), (0.7, 0.8, 0.9), (0.9, 1.0, 1.0)],
        ],
        dtype=np.float64,
    )
    local_u = 0.37
    local_v = 0.42
    actual_raw_rgb = sample_model_gl_linear(tile_image, local_u, local_v)

    models, best_model_name, best_model_mae = evaluate_sampling_models(tile_image, local_u, local_v, actual_raw_rgb)

    assert best_model_name == "gl_linear"
    assert best_model_mae == 0.0
    assert models["nearest"]["mae"] > 0.0


def test_incremental_json_report_writes_partial_updates(tmp_path) -> None:
    report_path = tmp_path / "pixel_path_report.json"
    report = IncrementalJsonReport(report_path)

    report.mark("startup_complete", sample_mode="normal")
    report.update_fields(sample_count=24)
    report.upsert_sample("sample_01", classification="wedge_interior", mismatch_case="case6_all_match")

    payload = json.loads(report_path.read_text(encoding="utf-8"))
    assert payload["last_phase"]["phase"] == "startup_complete"
    assert payload["sample_count"] == 24
    assert payload["samples"][0]["sample_id"] == "sample_01"


def test_incremental_json_report_falls_back_when_atomic_replace_is_denied(tmp_path, monkeypatch) -> None:
    report_path = tmp_path / "pixel_path_report.json"
    report = IncrementalJsonReport(report_path)

    original_replace = type(report_path).replace

    def replace_with_permission_error(self, target):
        raise PermissionError("atomic replace denied")

    monkeypatch.setattr(type(report_path), "replace", replace_with_permission_error)
    report.mark("startup_complete", sample_mode="normal")

    payload = json.loads(report_path.read_text(encoding="utf-8"))
    assert payload["last_phase"]["phase"] == "startup_complete"
    monkeypatch.setattr(type(report_path), "replace", original_replace)


def test_summarize_model_mae_aggregates_best_fit_counts() -> None:
    summary = summarize_model_mae(
        [
            {
                "raw_tile_rgb_compare": {
                    "best_model": "gl_linear",
                    "sampling_model_mae": {"gl_linear": 0.01, "nearest": 0.11},
                }
            },
            {
                "raw_tile_rgb_compare": {
                    "best_model": "nearest",
                    "sampling_model_mae": {"gl_linear": 0.09, "nearest": 0.02},
                }
            },
        ]
    )

    assert summary["dominant_best_fit_model"] in {"gl_linear", "nearest"}
    assert summary["per_model"]["gl_linear"]["sample_count"] == 2
    assert summary["per_model"]["nearest"]["best_fit_count"] == 1


def test_coordinate_helpers_map_screenshot_to_readback_space() -> None:
    logical_x, logical_y = screenshot_to_logical_xy(120, 80, device_pixel_ratio=2.0)

    assert logical_x == 60.25
    assert logical_y == 40.25
    assert top_left_to_bottom_left_readback_y(80, framebuffer_height=200) == 119


def test_build_capture_coordinate_record_tracks_all_spaces() -> None:
    record = build_capture_coordinate_record(
        120,
        80,
        viewport_width_logical=320.0,
        viewport_height_logical=200.0,
        device_pixel_ratio=2.0,
        probe_width=3,
        probe_height=3,
    )

    assert record["screenshot_x"] == 120
    assert record["screenshot_y"] == 80
    assert record["logical_x"] == 60.25
    assert record["logical_y"] == 40.25
    assert record["framebuffer_x"] == 120
    assert record["framebuffer_y"] == 80
    assert record["opengl_readback_x"] == 120
    assert record["opengl_readback_y"] == 317
    assert record["probe_width"] == 3
    assert record["probe_height"] == 3