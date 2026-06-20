"""Diagnose asteroid blink slowness on Davida dataset. Diagnostic only."""
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
SAMPLE_COUNT = 10  # per-component sample; full-cache scan uses all frames


def _timed(label: str, func):
    start = time.perf_counter()
    result = func()
    elapsed = time.perf_counter() - start
    print(f"  {label}: {elapsed:.4f}s")
    return elapsed, result


def _setup_window(frame_count: int | None = None):
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from PySide6.QtWidgets import QApplication, QTableWidgetItem
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
    all_frames = sorted(DATASET.glob("*.fits"))
    frames = all_frames if frame_count is None else all_frames[:frame_count]

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
    window._asteroid_results_table.setItem(0, 0, QTableWidgetItem("(511) Davida"))
    window._asteroid_results_table.setCurrentCell(0, 0)

    return app, window, frames, config_dir, state_dir


def main() -> int:
    from PySide6.QtWidgets import QApplication
    from photometry_app.ui.workers import preload_asteroid_blink_frames

    if not DATASET.exists():
        print(f"Dataset not found: {DATASET}")
        return 1

    total_frames = len(list(DATASET.glob("*.fits")))
    print(f"Dataset: {DATASET}")
    print(f"Total FITS frames: {total_frames}")
    print()

    app, window, sample_frames, config_dir, state_dir = _setup_window(SAMPLE_COUNT)
    try:
        render_settings = window._current_asteroid_image_render_settings()
        print(f"Sample frames loaded: {len(window._asteroid_frame_paths)}")
        print(f"Timer interval ms: {window._asteroid_blink_timer.interval()}")
        print(f"Settings blink duration ms: {window._settings.asteroid_blink_frame_duration_ms}")
        print()

        print("=== Phase 1: preload (simulates first Blink click) ===")
        elapsed, preload_result = _timed(
            "preload_asteroid_blink_frames",
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
        print(f"  loaded={preload_result.loaded_frame_count} measured={preload_result.measured_frame_count}")
        print(f"  warnings={len(preload_result.warning_messages or [])}")
        if preload_result.warning_messages:
            for message in preload_result.warning_messages[:3]:
                print(f"    - {message}")
        print(f"  preload_ready={window._asteroid_blink_preload_ready()}")
        print()

        print("=== Phase 2: _advance_asteroid_blink_frame (real blink timer path) ===")
        window._asteroid_blink_button.blockSignals(True)
        window._asteroid_blink_button.setChecked(True)
        window._asteroid_blink_button.blockSignals(False)
        window._start_asteroid_blink_playback()
        advance_times: list[float] = []
        for step in range(len(sample_frames)):
            start = time.perf_counter()
            window._advance_asteroid_blink_frame()
            QApplication.processEvents()
            advance_times.append(time.perf_counter() - start)
            idx = window._asteroid_frame_selector.currentIndex()
            path = window._asteroid_frame_paths[idx]
            qimage = window._asteroid_cached_qimage(path, render_settings)
            blink_active = window._asteroid_blink_playback_active()
            print(
                f"  step {step + 1}: frame={idx + 1} advance={advance_times[-1]:.4f}s "
                f"blink_active={blink_active} qimage_hit={qimage is not None and not qimage.isNull()}"
            )
        print(f"  advance_mean={sum(advance_times) / len(advance_times):.4f}s")
        window._asteroid_blink_timer.stop()
        print()

        print("=== Phase 3: isolated costs during blink-active fast path (frame 2) ===")
        window._asteroid_blink_timer.start()
        window._set_active_asteroid_frame(1, refresh_image=False)
        _timed("_update_asteroid_frame_metadata", window._update_asteroid_frame_metadata)
        _timed("_update_asteroid_visible_limit_label", window._update_asteroid_visible_limit_label)
        _timed("_asteroid_display_for_image", lambda: window._asteroid_display_for_image(sample_frames[1]))
        _timed("_current_asteroid_image_overlays", lambda: window._current_asteroid_image_overlays(allow_live_measurements=False))
        _timed("_current_asteroid_frame_measurements", lambda: window._current_asteroid_frame_measurements(allow_live_compute=False))
        _timed("_refresh_asteroid_blink_frame_view", window._refresh_asteroid_blink_frame_view)
        _timed("_set_active_asteroid_frame(blink)", lambda: window._set_active_asteroid_frame(2, refresh_image=True))
        window._asteroid_blink_timer.stop()
        print()

        print("=== Phase 4: compare blink-active vs inactive on same frame ===")
        window._set_active_asteroid_frame(0, refresh_image=False)
        window._asteroid_blink_timer.start()
        _, t_blink = _timed("blink-active _set_active_asteroid_frame", lambda: window._set_active_asteroid_frame(3, refresh_image=True))
        window._asteroid_blink_timer.stop()
        _, t_normal = _timed("inactive _set_active_asteroid_frame", lambda: window._set_active_asteroid_frame(3, refresh_image=True))
        print(f"  speedup ratio inactive/blink-active: {t_normal / max(t_blink, 1e-9):.1f}x")
        print()

        print("=== Phase 5: cache miss path (clear qimage cache, blink still active) ===")
        window._asteroid_sequence_qimage_cache.clear()
        window._asteroid_preloaded_sequence_signature = None
        window._asteroid_blink_timer.start()
        miss_times: list[float] = []
        for index in range(min(3, len(sample_frames))):
            start = time.perf_counter()
            window._set_active_asteroid_frame(index, refresh_image=True)
            miss_times.append(time.perf_counter() - start)
            print(f"  frame {index + 1} cache_miss={miss_times[-1]:.4f}s")
        print(f"  cache_miss_mean={sum(miss_times) / len(miss_times):.4f}s")
        window._asteroid_blink_timer.stop()
    finally:
        window.close()
        config_dir.cleanup()
        state_dir.cleanup()

    print()
    print("=== Phase 6: full sequence cache audit (all Davida frames, no preload) ===")
    app2, window2, all_frames, config_dir2, state_dir2 = _setup_window(None)
    try:
        render_settings2 = window2._current_asteroid_image_render_settings()
        print(f"Frames in sequence: {len(all_frames)}")
        print(f"preload_ready_before_any_preload={window2._asteroid_blink_preload_ready()}")

        print()
        print("Preloading all frames (this may take a few minutes)...")
        start = time.perf_counter()
        full_preload = preload_asteroid_blink_frames(
            frame_paths=window2._asteroid_frame_paths,
            frame_metadata=window2._asteroid_frame_metadata,
            detection_result=window2._current_asteroid_detection_result,
            detection_generation=window2._asteroid_detection_generation,
            render_settings=render_settings2,
            preload_displays=True,
            preload_rendered_qimages=True,
            preload_measurements=True,
            max_parallel_workers=4,
        )
        preload_elapsed = time.perf_counter() - start
        window2._store_asteroid_blink_preload_result(full_preload)
        print(f"  full_preload_elapsed={preload_elapsed:.1f}s")
        print(f"  loaded={full_preload.loaded_frame_count}/{len(all_frames)}")
        print(f"  measured={full_preload.measured_frame_count}")
        print(f"  warnings={len(full_preload.warning_messages or [])}")
        print(f"  preload_ready={window2._asteroid_blink_preload_ready()}")

        missing_qimages = []
        for path in all_frames:
            qimage = window2._asteroid_cached_qimage(path, render_settings2)
            if qimage is None or qimage.isNull():
                missing_qimages.append(path.name)
        print(f"  missing_qimage_count={len(missing_qimages)}")
        if missing_qimages[:5]:
            print(f"  first_missing={missing_qimages[:5]}")

        window2._asteroid_blink_button.blockSignals(True)
        window2._asteroid_blink_button.setChecked(True)
        window2._asteroid_blink_button.blockSignals(False)
        window2._start_asteroid_blink_playback()

        print()
        print("Sampling advance timings across full sequence (every 10th frame)...")
        sample_indices = list(range(0, len(all_frames), max(1, len(all_frames) // 10)))[:11]
        sampled_times: list[float] = []
        for target_index in sample_indices:
            window2._asteroid_frame_selector.blockSignals(True)
            window2._asteroid_frame_selector.setCurrentIndex(target_index)
            window2._asteroid_frame_selector.blockSignals(False)
            start = time.perf_counter()
            window2._advance_asteroid_blink_frame()
            QApplication.processEvents()
            sampled_times.append(time.perf_counter() - start)
            path = window2._asteroid_frame_paths[window2._asteroid_frame_selector.currentIndex()]
            qimage = window2._asteroid_cached_qimage(path, render_settings2)
            print(
                f"  frame {target_index + 1}: advance={sampled_times[-1]:.4f}s "
                f"qimage_hit={qimage is not None and not qimage.isNull()}"
            )
        print(f"  sampled_advance_mean={sum(sampled_times) / len(sampled_times):.4f}s")

        print()
        print("=== Phase 7: without preload, first 3 frames (cold path) ===")
        window2._asteroid_sequence_qimage_cache.clear()
        window2._asteroid_sequence_display_cache.clear()
        window2._asteroid_preloaded_sequence_signature = None
        cold_times: list[float] = []
        for index in range(3):
            start = time.perf_counter()
            window2._set_active_asteroid_frame(index, refresh_image=True)
            cold_times.append(time.perf_counter() - start)
            print(f"  cold frame {index + 1}: {cold_times[-1]:.4f}s")
        print(f"  cold_mean={sum(cold_times) / len(cold_times):.4f}s")
    finally:
        window2.close()
        config_dir2.cleanup()
        state_dir2.cleanup()

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
