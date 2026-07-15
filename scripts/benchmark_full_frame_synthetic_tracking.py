from __future__ import annotations

import argparse
from datetime import UTC, datetime, timedelta
from pathlib import Path
from time import perf_counter
from unittest.mock import patch

import numpy as np

from photometry_app.core.settings import AppSettings, resolve_shared_parallel_workers
from photometry_app.core.synthetic_tracking import build_synthetic_tracked_full_frame_stack


def _synthetic_frame(size: int, center_x: float, center_y: float, *, seed: int) -> np.ndarray:
    rng = np.random.default_rng(seed)
    image = rng.normal(0.0, 1.0, size=(size, size)).astype(np.float32)
    yy, xx = np.indices(image.shape, dtype=float)
    signal = 10.0 * np.exp(-(((xx - center_x) ** 2) + ((yy - center_y) ** 2)) / (2.0 * (1.1 ** 2)))
    return image + signal.astype(np.float32)


def _build_benchmark_frames(frame_count: int, image_size: int, motion_px_per_hour: float) -> tuple[list[Path], dict[str, datetime], dict[str, np.ndarray]]:
    frame_paths = [Path(f"benchmark_full_frame_{index:02d}.fits") for index in range(frame_count)]
    observation_start = datetime(2025, 1, 14, 21, 12, tzinfo=UTC)
    frame_times = {
        str(path.resolve()): observation_start + timedelta(minutes=index)
        for index, path in enumerate(frame_paths)
    }
    pixels_per_minute = float(motion_px_per_hour) / 60.0
    center_y = image_size / 2.0
    center_x = (image_size / 2.0) - ((frame_count - 1) * pixels_per_minute / 2.0)
    images = {
        str(path.resolve()): _synthetic_frame(
            image_size,
            center_x + (index * pixels_per_minute),
            center_y,
            seed=index + 1,
        )
        for index, path in enumerate(frame_paths)
    }
    return frame_paths, frame_times, images


def run_benchmark(
    *,
    frame_count: int,
    image_size: int,
    repeats: int,
    warmup_runs: int,
    integration_mode: str,
    rejection_mode: str,
    backend: str,
    motion_px_per_hour: float,
    max_parallel_workers: int,
) -> tuple[list[float], str]:
    frame_paths, frame_times, images = _build_benchmark_frames(frame_count, image_size, motion_px_per_hour)

    def image_side_effect(source_path: Path) -> np.ndarray:
        return images[str(source_path.resolve())]

    durations: list[float] = []
    summary_text = ""
    with patch("photometry_app.core.synthetic_tracking.read_photometry_image_data", side_effect=image_side_effect):
        for _ in range(max(0, warmup_runs)):
            build_synthetic_tracked_full_frame_stack(
                frame_paths,
                reference_path=frame_paths[0],
                frame_observation_times=frame_times,
                motion_px_per_hour=motion_px_per_hour,
                motion_angle_deg=0.0,
                integration_mode=integration_mode,
                rejection_mode=rejection_mode,
                array_backend_preference=backend,
                max_parallel_workers=max_parallel_workers,
            )
        for _ in range(max(1, repeats)):
            start = perf_counter()
            result = build_synthetic_tracked_full_frame_stack(
                frame_paths,
                reference_path=frame_paths[0],
                frame_observation_times=frame_times,
                motion_px_per_hour=motion_px_per_hour,
                motion_angle_deg=0.0,
                integration_mode=integration_mode,
                rejection_mode=rejection_mode,
                array_backend_preference=backend,
                max_parallel_workers=max_parallel_workers,
            )
            durations.append(perf_counter() - start)
            summary_text = result.summary_text
    return durations, summary_text


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Benchmark full-frame synthetic tracking only. This intentionally does not benchmark crop-mode stacking.",
    )
    parser.add_argument("--frames", type=int, default=12, help="Number of synthetic full-frame inputs to generate.")
    parser.add_argument("--size", type=int, default=1024, help="Square image size in pixels.")
    parser.add_argument("--repeats", type=int, default=3, help="How many benchmark runs to execute.")
    parser.add_argument(
        "--warmup-runs",
        type=int,
        default=None,
        help="How many untimed warm-up full-frame runs to execute before benchmarking. Defaults to 1 for GPU benchmarks and 0 otherwise.",
    )
    parser.add_argument("--integration", choices=["average", "mean", "min", "max"], default="average")
    parser.add_argument(
        "--rejection",
        choices=["no_rejection", "min_max", "sigma_clipping", "winsorized_sigma_clipping", "averaged_sigma_clipping"],
        default="no_rejection",
    )
    parser.add_argument("--backend", choices=["auto", "cpu", "gpu"], default="auto")
    parser.add_argument("--motion-px-per-hour", type=float, default=30.0)
    parser.add_argument(
        "--workers",
        type=int,
        default=None,
        help="Override the max worker count passed into full-frame Synthetic Track. Defaults to the current saved shared worker setting.",
    )
    args = parser.parse_args()
    warmup_runs = max(0, int(args.warmup_runs)) if args.warmup_runs is not None else (1 if args.backend == "gpu" else 0)
    settings = AppSettings.from_root(Path.cwd())
    max_parallel_workers = max(0, int(args.workers)) if args.workers is not None else resolve_shared_parallel_workers(settings)

    durations, summary_text = run_benchmark(
        frame_count=max(1, args.frames),
        image_size=max(32, args.size),
        repeats=max(1, args.repeats),
        warmup_runs=warmup_runs,
        integration_mode=args.integration,
        rejection_mode=args.rejection,
        backend=args.backend,
        motion_px_per_hour=float(args.motion_px_per_hour),
        max_parallel_workers=max_parallel_workers,
    )
    durations_ms = [duration * 1000.0 for duration in durations]
    print(f"Benchmark target: full-frame synthetic tracking only")
    print(f"Backend preference: {args.backend}")
    print(f"Max parallel workers: {max_parallel_workers}")
    print(f"Warm-up runs: {warmup_runs}")
    print(f"Integration: {args.integration}")
    print(f"Rejection: {args.rejection}")
    print(f"Frames: {args.frames}")
    print(f"Image size: {args.size}x{args.size}")
    print(f"Durations (ms): {', '.join(f'{value:.2f}' for value in durations_ms)}")
    print(f"Best (ms): {min(durations_ms):.2f}")
    print(f"Mean (ms): {sum(durations_ms) / len(durations_ms):.2f}")
    print(f"Summary: {summary_text}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())