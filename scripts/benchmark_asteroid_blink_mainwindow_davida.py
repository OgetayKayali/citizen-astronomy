"""MainWindow-level asteroid blink benchmark on Davida dataset. Diagnostic only."""
from __future__ import annotations

import os
import sys
import tempfile
import time
from datetime import UTC, datetime
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

DATASET = Path(r"E:\Davida\aligned")
FRAME_COUNT = 5


def _timed(label: str, func):
    start = time.perf_counter()
    result = func()
    elapsed = time.perf_counter() - start
    print(f"  {label}: {elapsed:.3f}s")
    return elapsed, result


def _setup_window():
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from PySide6.QtWidgets import QApplication
    from photometry_app.core.models import FileScanResult, ObservationMetadata, SolvedField, WcsStatus
    from photometry_app.core.solar_system import SolarSystemDetection, SolarSystemDetectionResult
    from photometry_app.ui.main_window import MainWindow

    app = QApplication.instance() or QApplication([])

    config_dir = tempfile.TemporaryDirectory()
    state_dir = tempfile.TemporaryDirectory()
    config_path = Path(config_dir.name) / "settings.json"
    state_path = Path(state_dir.name) / "state.json"
    config_path.write_text("{}", encoding="utf-8")
    state_path.write_text("{}", encoding="utf-8")
    os.environ["CITIZEN_PHOTOMETRY_CONFIG_PATH"] = str(config_path)
    os.environ["CITIZEN_PHOTOMETRY_STATE_PATH"] = str(state_path)
    os.environ["CITIZEN_PHOTOMETRY_TRAINING_DB"] = str(Path(state_dir.name) / "training.sqlite3")

    window = MainWindow()
    window._root_path_input.setText(str(DATASET.parent))

    frames = sorted(DATASET.glob("*.fits"))[:FRAME_COUNT]
    metadata = ObservationMetadata(
        date_obs=datetime(2026, 5, 9, 23, 41, 48, tzinfo=UTC),
        filter_name="L",
        exposure_seconds=120.0,
        width=6248,
        height=4176,
        object_name="Davida",
        frame_type="Light",
    )
    scan_result = FileScanResult(path=frames[0], object_folder="Davida", metadata=metadata, wcs_status=WcsStatus.SOLVED)

    with patch("photometry_app.ui.main_window.inspect_fits_file", return_value=scan_result):
        window._set_asteroid_sequence_paths(frames, source_label=DATASET)

    detection = SolarSystemDetection(
        name="(511) Davida",
        designation="511",
        object_type="Asteroid",
        orbit_class="MBA",
        predicted_ra_deg=170.0,
        predicted_dec_deg=10.0,
        predicted_x=3124.0,
        predicted_y=2088.0,
        predicted_magnitude=9.5,
        ra_rate_arcsec_per_hour=5.0,
        dec_rate_arcsec_per_hour=1.0,
        motion_rate_arcsec_per_hour=5.1,
        expected_trail_length_px=0.5,
        positional_uncertainty_arcsec=0.5,
        altitude_deg=55.0,
        likely_visible=True,
        confidence_score=0.95,
        status="Likely visible",
    )
    solved_field = SolvedField(
        center_ra_deg=170.0,
        center_dec_deg=10.0,
        radius_deg=0.25,
        width=6248,
        height=4176,
        wcs_path=frames[0],
    )
    window._current_asteroid_detection_result = SolarSystemDetectionResult(
        source_path=frames[0],
        solved_field=solved_field,
        observation_time=metadata.date_obs,
        prediction_time=metadata.date_obs,
        exposure_seconds=120.0,
        filter_name="L",
        pixel_scale_arcsec_per_pixel=1.0,
        field_width_deg=0.5,
        field_height_deg=0.35,
        magnitude_limit=18.0,
        used_astrometry_fallback=False,
        detections=[detection],
        summary_text="test",
    )
    window._asteroid_detection_generation += 1
    window._selected_asteroid_detection_index = 0
    window._asteroid_results_table.setRowCount(1)
    window._asteroid_results_table.setItem(0, 0, __import__("PySide6.QtWidgets", fromlist=["QTableWidgetItem"]).QTableWidgetItem("(511) Davida"))
    window._asteroid_results_table.setCurrentCell(0, 0)

    return app, window, frames, config_dir, state_dir


def main() -> int:
    from photometry_app.ui.workers import preload_asteroid_blink_frames

    if not DATASET.exists():
        print(f"Dataset not found: {DATASET}")
        return 1

    app, window, frames, config_dir, state_dir = _setup_window()
    try:
        render_settings = window._current_asteroid_image_render_settings()
        print(f"Loaded {len(window._asteroid_frame_paths)} frames into asteroid mode")
        print(f"Preload ready before preload: {window._asteroid_blink_preload_ready()}")
        print()

        print("=== Blink preload (what runs when you first click Blink) ===")
        elapsed, preload_result = _timed(
            "preload_asteroid_blink_frames(all frames)",
            lambda: preload_asteroid_blink_frames(
                frame_paths=window._asteroid_frame_paths,
                frame_metadata=window._asteroid_frame_metadata,
                detection_result=window._current_asteroid_detection_result,
                detection_generation=window._asteroid_detection_generation,
                render_settings=render_settings,
                preload_displays=True,
                preload_rendered_qimages=True,
                preload_measurements=True,
                max_parallel_workers=4,
            ),
        )
        window._store_asteroid_blink_preload_result(preload_result)
        print(f"  loaded_frame_count={preload_result.loaded_frame_count} measured_frame_count={preload_result.measured_frame_count}")
        print(f"  Preload ready after preload: {window._asteroid_blink_preload_ready()}")
        print()

        print("=== Per-frame playback costs WITH preload cache (first loop) ===")
        first_loop_total = 0.0
        for index in range(len(frames)):
            print(f"Frame {index + 1}: {frames[index].name}")
            qimage = window._asteroid_cached_qimage(frames[index], render_settings)
            print(f"  cached_qimage_hit={qimage is not None and not qimage.isNull()}")

            elapsed, _ = _timed(
                "_set_active_asteroid_frame (blink path, with detection)",
                lambda i=index: window._set_active_asteroid_frame(i, refresh_image=True),
            )
            first_loop_total += elapsed
        print(f"  first_loop_total={first_loop_total:.3f}s mean={first_loop_total / len(frames):.3f}s")
        print()

        print("=== Per-frame playback costs WITH preload cache (second loop) ===")
        second_loop_total = 0.0
        for index in range(len(frames)):
            print(f"Frame {index + 1}: {frames[index].name}")
            elapsed, _ = _timed(
                "_set_active_asteroid_frame (second pass)",
                lambda i=index: window._set_active_asteroid_frame(i, refresh_image=True),
            )
            second_loop_total += elapsed
        print(f"  second_loop_total={second_loop_total:.3f}s mean={second_loop_total / len(frames):.3f}s")
        print()

        print("=== Isolated component timings on frame 2 ===")
        window._set_active_asteroid_frame(1, refresh_image=False)
        frame_path = frames[1]
        _timed("_refresh_asteroid_sky_view", lambda: window._refresh_asteroid_sky_view())
        _timed("_refresh_asteroid_image_view", lambda: window._refresh_asteroid_image_view(reset_view=False, allow_live_measurements=False))
        _timed("_handle_asteroid_result_selection_changed", lambda: window._handle_asteroid_result_selection_changed(allow_live_measurements=False))
        _timed("_current_asteroid_frame_measurements", lambda: window._current_asteroid_frame_measurements(allow_live_compute=False))
        _timed("_current_asteroid_image_overlays", lambda: window._current_asteroid_image_overlays(allow_live_measurements=False))
        print()

        print("=== Playback WITHOUT preload cache (simulate cache miss) ===")
        window._asteroid_sequence_qimage_cache.clear()
        window._asteroid_preloaded_sequence_signature = None
        window._asteroid_sequence_display_cache.clear()
        print(f"  Preload ready after cache clear: {window._asteroid_blink_preload_ready()}")
        miss_total = 0.0
        for index in range(min(3, len(frames))):
            print(f"Frame {index + 1}: {frames[index].name}")
            qimage = window._asteroid_cached_qimage(frames[index], render_settings)
            print(f"  cached_qimage_hit={qimage is not None and not qimage.isNull()}")
            elapsed, _ = _timed(
                "_set_active_asteroid_frame (cache miss)",
                lambda i=index: window._set_active_asteroid_frame(i, refresh_image=True),
            )
            miss_total += elapsed
        print(f"  cache_miss_mean={miss_total / min(3, len(frames)):.3f}s")
    finally:
        window.close()
        config_dir.cleanup()
        state_dir.cleanup()

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
