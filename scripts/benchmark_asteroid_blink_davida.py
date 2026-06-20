"""Benchmark asteroid blink frame costs on a real dataset. Diagnostic only."""
from __future__ import annotations

import os
import sys
import time
from pathlib import Path

# Project root on path
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

DATASET = Path(r"E:\Davida\aligned")
FRAME_SAMPLE_COUNT = 5
SECOND_PASS_FRAMES = 3


def _timed(label: str, func):
    start = time.perf_counter()
    result = func()
    elapsed = time.perf_counter() - start
    return elapsed, result


def _frame_paths(limit: int) -> list[Path]:
    paths = sorted(
        path
        for path in DATASET.iterdir()
        if path.is_file() and path.suffix.lower() in {".fits", ".fit", ".fts"}
    )
    return paths[:limit]


def main() -> int:
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

    from PySide6.QtWidgets import QApplication

    app = QApplication.instance() or QApplication([])

    from photometry_app.core.image_io import read_image_data
    from photometry_app.core.plotting import (
        AnnotatedImageRenderSettings,
        build_annotated_image_display,
        build_placeholder_annotated_image_display,
        render_image_path_for_display,
    )
    from photometry_app.core.settings import AppSettings
    from photometry_app.core.sky_explorer import explore_sky_image
    from photometry_app.ui.workers import _blink_frame_cache_key, _rendered_blink_qimage

    if not DATASET.exists():
        print(f"Dataset not found: {DATASET}")
        return 1

    frames = _frame_paths(FRAME_SAMPLE_COUNT)
    if len(frames) < 2:
        print(f"No FITS frames found in {DATASET}")
        return 1

    render_settings = AnnotatedImageRenderSettings()
    settings = AppSettings.defaults(DATASET)

    print(f"Dataset: {DATASET}")
    print(f"Total FITS frames: {len(list(DATASET.glob('*.fits')))}")
    print(f"Sample size: {len(frames)}")
    print(f"First frame: {frames[0].name} ({frames[0].stat().st_size / (1024 * 1024):.1f} MiB)")
    print()

    # Image dimensions
    sample_data = read_image_data(frames[0], dtype=None)
    print(f"Frame shape: {getattr(sample_data, 'shape', None)} dtype={getattr(sample_data, 'dtype', None)}")
    print()

    results: dict[str, list[float]] = {}

    def record(name: str, elapsed: float) -> None:
        results.setdefault(name, []).append(elapsed)
        print(f"  {name}: {elapsed:.3f}s")

    print("=== Per-frame costs (first pass) ===")
    cache_keys: list[tuple[str, int, int]] = []
    qimages = {}
    for index, frame_path in enumerate(frames, start=1):
        print(f"Frame {index}: {frame_path.name}")
        cache_key = _blink_frame_cache_key(frame_path)
        cache_keys.append(cache_key)

        elapsed, _ = _timed("read_image_data", lambda p=frame_path: read_image_data(p, dtype=None))
        record("read_image_data", elapsed)

        elapsed, _ = _timed(
            "render_image_path_for_display",
            lambda p=frame_path: render_image_path_for_display(p, render_settings),
        )
        record("render_image_path_for_display", elapsed)

        elapsed, qimage = _timed(
            "_rendered_blink_qimage",
            lambda p=frame_path: _rendered_blink_qimage(p, render_settings),
        )
        qimages[cache_key] = qimage
        record("_rendered_blink_qimage", elapsed)

        elapsed, _ = _timed(
            "build_annotated_image_display",
            lambda p=frame_path: build_annotated_image_display(p),
        )
        record("build_annotated_image_display", elapsed)

        elapsed, _ = _timed(
            "build_placeholder_annotated_image_display",
            lambda p=frame_path: build_placeholder_annotated_image_display(p),
        )
        record("build_placeholder_annotated_image_display", elapsed)

        elapsed, _ = _timed(
            "explore_sky_image (asteroid sky view)",
            lambda p=frame_path: explore_sky_image(
                p,
                settings=settings,
                selected_layers=("gaia_stars",),
                gaia_object_limit=250,
            ),
        )
        record("explore_sky_image", elapsed)
        print()

    print("=== Cached blink playback simulation (second pass) ===")
    for index, frame_path in enumerate(frames[:SECOND_PASS_FRAMES], start=1):
        print(f"Frame {index}: {frame_path.name}")
        cache_key = _blink_frame_cache_key(frame_path)
        qimage = qimages[cache_key]

        elapsed, display = _timed(
            "placeholder_display_lookup",
            lambda p=frame_path: build_placeholder_annotated_image_display(p),
        )
        record("placeholder_display_lookup", elapsed)

        elapsed, _ = _timed(
            "cached_qimage_present",
            lambda q=qimage: q is not None and not q.isNull(),
        )
        record("cached_qimage_present_check", elapsed)

        # Simulate image view fast path: placeholder + pre-rendered qimage only
        from photometry_app.ui.image_view import AnnotatedImageView

        view = AnnotatedImageView()
        elapsed, _ = _timed(
            "image_view.set_content(pre_rendered_qimage)",
            lambda d=display, q=qimage: view.set_content(
                d,
                overlays=[],
                grid_overlays=[],
                editor_enabled=False,
                reset_view=False,
                render_settings=render_settings,
                pre_rendered_qimage=q,
            ),
        )
        record("image_view_set_content_cached", elapsed)

        # Simulate slow fallback path (no pre-rendered qimage)
        elapsed, _ = _timed(
            "image_view.set_content(full_render)",
            lambda d=display: view.set_content(
                d,
                overlays=[],
                grid_overlays=[],
                editor_enabled=False,
                reset_view=False,
                render_settings=render_settings,
                pre_rendered_qimage=None,
            ),
        )
        record("image_view_set_content_full_render", elapsed)
        print()

    print("=== explore_sky_image second pass (should use asteroid sky cache in app) ===")
    for index, frame_path in enumerate(frames[:SECOND_PASS_FRAMES], start=1):
        print(f"Frame {index}: {frame_path.name}")
        elapsed, _ = _timed(
            "explore_sky_image_repeat",
            lambda p=frame_path: explore_sky_image(
                p,
                settings=settings,
                selected_layers=("gaia_stars",),
                gaia_object_limit=250,
            ),
        )
        record("explore_sky_image_repeat", elapsed)
    print()

    print("=== Summary (mean seconds) ===")
    for name, values in results.items():
        mean_value = sum(values) / len(values)
        print(f"{name:40s} {mean_value:8.3f}s  (n={len(values)})")

    total_render_first_pass = sum(results.get("render_image_path_for_display", []))
    total_cached_playback = sum(results.get("image_view_set_content_cached", []))
    total_full_render_playback = sum(results.get("image_view_set_content_full_render", []))
    print()
    print(f"If blink misses preload cache, ~{total_render_first_pass / max(1, len(frames)):.2f}s per frame from render alone.")
    print(f"Cached playback path mean: {total_cached_playback / max(1, SECOND_PASS_FRAMES):.3f}s per frame.")
    print(f"Full-render playback path mean: {total_full_render_playback / max(1, SECOND_PASS_FRAMES):.3f}s per frame.")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
