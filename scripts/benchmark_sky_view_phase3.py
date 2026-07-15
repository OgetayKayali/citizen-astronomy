"""Phase-3 Sky Atlas interactive performance benchmark.

Runs pan-drag, zoom, and stable-twinkle scenarios and prints avg/p95 paint times.
"""

from __future__ import annotations

import argparse
import os
import statistics
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))


def _percentile(sorted_values: list[float], pct: float) -> float:
    if not sorted_values:
        return 0.0
    if len(sorted_values) == 1:
        return sorted_values[0]
    rank = (len(sorted_values) - 1) * (pct / 100.0)
    low = int(rank)
    high = min(len(sorted_values) - 1, low + 1)
    weight = rank - low
    return sorted_values[low] * (1.0 - weight) + sorted_values[high] * weight


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--frames", type=int, default=300)
    parser.add_argument("--width", type=int, default=1280)
    parser.add_argument("--height", type=int, default=800)
    parser.add_argument("--no-milky-way", action="store_true", help="Disable Milky Way for star-focused timings")
    args = parser.parse_args()

    os.environ.setdefault("CITIZEN_PHOTOMETRY_SKY_VIEW_TIMING", "1")

    from PySide6.QtWidgets import QApplication
    from PySide6.QtCore import QPoint
    from PySide6.QtGui import QSurfaceFormat

    from photometry_app.core.sky_atlas import load_local_sky_atlas_objects, load_sky_atlas_objects
    from photometry_app.ui.main_window import _SkyAtlasViewWidget
    from scripts.run_sky_view_benchmark import (
        SkyViewBenchmarkHarness,
        _configure_pan_drag,
        _configure_zoom,
        _step_pan_drag,
        _step_zoom,
    )

    app = QApplication.instance() or QApplication([])
    fmt = QSurfaceFormat()
    fmt.setRenderableType(QSurfaceFormat.RenderableType.OpenGL)
    QSurfaceFormat.setDefaultFormat(fmt)

    canvas = _SkyAtlasViewWidget()
    canvas.resize(args.width, args.height)
    canvas.show()
    app.processEvents()

    # Catalog root is <cache_dir>/sky-atlas. Repo ships packaged surveys under ./sky-atlas.
    try:
        sky_objects = load_sky_atlas_objects(PROJECT_ROOT, download_if_missing=False)
    except Exception:
        sky_objects = load_local_sky_atlas_objects()
    star_count = sum(1 for item in sky_objects if str(item.object_type).casefold() == "star")
    if star_count < 1000:
        print(f"warning: only {star_count} stars loaded (expected Hipparcos under sky-atlas/)")
    canvas.set_objects(sky_objects)
    app.processEvents()

    harness = SkyViewBenchmarkHarness(
        app,
        canvas,
        recorder=None,
        width=args.width,
        height=args.height,
        qpoint_type=QPoint,
        sky_objects=tuple(sky_objects),
    )

    scenarios = (
        ("drag", _configure_pan_drag, _step_pan_drag),
        ("zoom", _configure_zoom, _step_zoom),
        ("stable_twinkle", None, None),
    )

    print(
        f"phase3_perf_benchmark frames={args.frames} objects={len(sky_objects)} stars_in_catalog={star_count}"
    )
    stage_attrs = (
        "collect_seconds",
        "partition_seconds",
        "grid_seconds",
        "fast_star_seconds",
        "overlay_seconds",
        "label_seconds",
        "ground_seconds",
        "moon_seconds",
    )
    for name, configure, step in scenarios:
        if configure is not None:
            configure(harness)
        else:
            harness.configure_base()
            # Orion, moderate FOV: enough Hipparcos density for twinkle timing.
            harness.set_camera_radec(83.8, -5.4, 25.0)
            canvas._star_twinkle_amount = 0.5
            canvas._drag_origin = None
            canvas._ground_layer_interaction_defer_active = False
            canvas._camera_animation_timer.stop()
        if args.no_milky_way:
            canvas.set_milky_way_enabled(False)

        # Discard cold load / MW tile frames.
        for _warmup in range(8):
            canvas.repaint()
            app.processEvents()

        paint_samples: list[float] = []
        star_samples: list[float] = []
        upload_samples: list[int] = []
        backend = "unknown"
        visible_stars = 0
        max_upload_bytes = 0
        stage_totals = {attr: 0.0 for attr in stage_attrs}
        window = getattr(canvas, "_sky_view_timing_window", None)
        prev_stages = {attr: float(getattr(window, attr, 0.0) or 0.0) for attr in stage_attrs} if window else {}

        for frame_index in range(args.frames):
            if step is not None:
                step(harness, frame_index, "measured")
            else:
                canvas.update()
            app.processEvents()
            canvas.repaint()
            app.processEvents()
            total = float(getattr(canvas, "_sky_view_last_paint_total_seconds", 0.0) or 0.0)
            if total > 0.0:
                paint_samples.append(total)
            log_line = str(getattr(canvas, "_sky_view_last_star_renderer_log", "") or "")
            if "backend=" in log_line:
                backend = log_line.split("backend=", 1)[1].split()[0]
            metrics = getattr(getattr(canvas, "_star_renderer", None), "metrics", None)
            if metrics is not None:
                star_ms = (
                    float(getattr(metrics, "prepare_seconds", 0.0))
                    + float(getattr(metrics, "upload_seconds", 0.0))
                    + float(getattr(metrics, "compact_draw_seconds", 0.0))
                    + float(getattr(metrics, "halo_draw_seconds", 0.0))
                    + float(getattr(metrics, "tonemap_seconds", 0.0))
                )
                star_samples.append(star_ms)
                uploaded = int(getattr(metrics, "uploaded_bytes", 0) or 0)
                upload_samples.append(uploaded)
                max_upload_bytes = max(max_upload_bytes, uploaded)
                visible_stars = max(visible_stars, int(getattr(metrics, "visible_star_count", 0) or 0))
            else:
                visible_stars = max(
                    visible_stars,
                    int(getattr(canvas, "_sky_view_last_visible_star_count", 0) or 0),
                )
            window = getattr(canvas, "_sky_view_timing_window", None)
            if window is not None:
                for attr in stage_attrs:
                    current = float(getattr(window, attr, 0.0) or 0.0)
                    delta = current - prev_stages.get(attr, 0.0)
                    if delta > 0.0:
                        stage_totals[attr] += delta
                    prev_stages[attr] = current

        if not paint_samples and not star_samples:
            print(f"{name}: no samples collected backend={backend}")
            continue

        paint_ordered = sorted(paint_samples)
        paint_avg = statistics.fmean(paint_ordered) if paint_ordered else 0.0
        paint_p95 = _percentile(paint_ordered, 95.0) if paint_ordered else 0.0
        fps = 1.0 / max(paint_avg, 1.0e-6) if paint_ordered else 0.0
        star_ordered = sorted(star_samples)
        star_avg = statistics.fmean(star_ordered) if star_ordered else 0.0
        star_p95 = _percentile(star_ordered, 95.0) if star_ordered else 0.0
        n = max(1, len(paint_ordered))
        top3 = sorted(stage_totals.items(), key=lambda item: item[1], reverse=True)[:3]
        top3_text = ", ".join(f"{key}={value * 1000.0 / n:.2f}ms" for key, value in top3) or "-"
        avg_upload = int(statistics.fmean(upload_samples)) if upload_samples else 0
        print(
            f"{name}: paint_avg={paint_avg*1000:.2f}ms paint_p95={paint_p95*1000:.2f}ms fps~={fps:.1f} "
            f"star_avg={star_avg*1000:.2f}ms star_p95={star_p95*1000:.2f}ms "
            f"n={len(paint_ordered)} stars={visible_stars} backend={backend} "
            f"upload_bytes_avg={avg_upload} upload_bytes_max={max_upload_bytes}"
        )
        print(f"  top3_cpu=[{top3_text}]")
        print(f"  star_renderer_log={getattr(canvas, '_sky_view_last_star_renderer_log', '')}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
