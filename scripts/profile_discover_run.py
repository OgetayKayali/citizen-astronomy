from __future__ import annotations

import argparse
from pathlib import Path
import sys

from photometry_app.core.discovery_benchmark import run_discovery_benchmark
from photometry_app.core.settings import AppSettings


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Profile asteroid/comet Discover on a solved frame folder using the current app settings.",
    )
    parser.add_argument("folder", type=Path, help="Folder containing one solved asteroid/comet frame group.")
    parser.add_argument(
        "--workspace-root",
        type=Path,
        default=Path.cwd(),
        help="Workspace root used to load the persisted photometry settings. Defaults to the current directory.",
    )
    parser.add_argument("--filter", dest="filter_name", default=None, help="Filter name when the folder contains multiple groups.")
    parser.add_argument("--exposure", type=float, default=None, help="Exposure seconds when the folder contains multiple groups.")
    parser.add_argument("--reference", default=None, help="Reference frame filename inside the selected group.")
    parser.add_argument("--assume-aligned", action="store_true", help="Skip reprojection and benchmark Discover as already aligned.")
    parser.add_argument(
        "--top",
        type=int,
        default=30,
        help="Number of cumulative cProfile entries to print. Defaults to 30.",
    )
    parser.add_argument(
        "--profile-out",
        type=Path,
        default=None,
        help="Optional path for the raw cProfile .prof output.",
    )
    args = parser.parse_args()

    settings = AppSettings.from_root(args.workspace_root.expanduser().resolve())
    report = run_discovery_benchmark(
        args.folder,
        settings=settings,
        filter_name=args.filter_name,
        exposure_seconds=args.exposure,
        reference_name=args.reference,
        assume_aligned=args.assume_aligned,
        top_profile_functions=args.top,
        profile_output_path=args.profile_out,
    )

    print(f"Group: {report.group_label}")
    print(f"Frames: {report.frame_count}")
    print(f"Reference: {report.reference_path.name}")
    print(f"Known detections after Generate: {len(report.known_detection_result.detections)}")
    print(f"Generate time: {report.generate_seconds:.2f}s")
    if report.estimate_error is None:
        if report.estimate_result is not None and report.estimate_seconds is not None:
            print(
                "Visible-limit estimate: "
                f"{report.estimate_result.dimmest_visible_magnitude:.1f} mag in {report.estimate_seconds:.2f}s"
            )
    else:
        print(f"Visible-limit estimate failed after {report.estimate_seconds:.2f}s: {report.estimate_error}")
    print(f"Discover time: {report.discover_seconds:.2f}s")
    print(
        "Discover result: "
        f"{report.discovery_result.recovered_known_count} recovered known, "
        f"{report.discovery_result.candidate_count} potential, "
        f"{len(report.discovery_result.review_candidates)} review"
    )
    print()
    print("Progress timeline:")
    for event in report.progress_events:
        print(f"[{event.elapsed_seconds:8.2f}s] {event.message}")
    if report.profile_stats_text:
        print()
        print("Top cumulative cProfile entries:")
        print(report.profile_stats_text)
    if args.profile_out is not None:
        print()
        print(f"Raw cProfile stats written to: {args.profile_out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())