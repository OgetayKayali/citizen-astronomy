from __future__ import annotations

import argparse
from datetime import UTC, datetime, timedelta
from pathlib import Path

from photometry_app.core.solar_system import search_bright_solar_system_objects_globally


def _parse_utc(value: str) -> datetime:
    text = value.strip()
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    parsed = datetime.fromisoformat(text)
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=UTC)
    return parsed.astimezone(UTC)


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Build or refresh a local bright-object planning database for Plan Object blank-search mode."
    )
    parser.add_argument("--cache-dir", default=".photometry-cache", help="App cache directory that will hold the SQLite snapshot database.")
    parser.add_argument("--start-utc", default=datetime.now(UTC).replace(hour=0, minute=0, second=0, microsecond=0).isoformat(), help="Start UTC for snapshot generation (ISO 8601).")
    parser.add_argument("--end-utc", default=(datetime.now(UTC).replace(hour=0, minute=0, second=0, microsecond=0) + timedelta(days=365 * 3)).isoformat(), help="End UTC for snapshot generation (ISO 8601).")
    parser.add_argument("--step-hours", type=float, default=24.0, help="UTC spacing between generated snapshots.")
    parser.add_argument("--magnitude-limit", type=float, default=18.0, help="Stored predicted-magnitude limit.")
    parser.add_argument("--max-results", type=int, default=100, help="Maximum ranked bright candidates stored per snapshot.")
    parser.add_argument("--observatory-code", default=None, help="Optional MPC observatory code for the stored predictions.")
    parser.add_argument("--skip-asteroids", action="store_true", help="Do not include asteroids in stored snapshots.")
    parser.add_argument("--skip-comets", action="store_true", help="Do not include comets in stored snapshots.")
    parser.add_argument("--max-parallel-workers", type=int, default=0, help="Worker count for the live SkyBoT scan used to populate missing snapshots. 0 keeps auto mode.")
    args = parser.parse_args()

    start_time = _parse_utc(args.start_utc)
    end_time = _parse_utc(args.end_utc)
    if end_time < start_time:
        raise SystemExit("--end-utc must be on or after --start-utc")
    if args.step_hours <= 0:
        raise SystemExit("--step-hours must be greater than zero")
    include_asteroids = not args.skip_asteroids
    include_comets = not args.skip_comets
    if not include_asteroids and not include_comets:
        raise SystemExit("At least one of asteroids or comets must be included.")

    cache_dir = Path(args.cache_dir).expanduser()
    snapshot_count = 0
    current_time = start_time
    while current_time <= end_time:
        print(f"[{snapshot_count + 1}] Building bright-object snapshot for {current_time:%Y-%m-%d %H:%M UTC}...")
        results = search_bright_solar_system_objects_globally(
            observation_time=current_time,
            observer_latitude_deg=None,
            observer_longitude_deg=None,
            observer_elevation_m=None,
            magnitude_limit=float(args.magnitude_limit),
            observatory_code=str(args.observatory_code).strip() or None,
            include_asteroids=include_asteroids,
            include_comets=include_comets,
            max_results=max(1, int(args.max_results)),
            max_parallel_workers=max(0, int(args.max_parallel_workers)),
            cache_dir=cache_dir,
            use_local_database=False,
            progress_callback=lambda message: print(f"    {message}"),
        )
        print(f"    Stored {len(results)} bright candidate(s).")
        current_time += timedelta(hours=float(args.step_hours))
        snapshot_count += 1

    print(f"Completed {snapshot_count} snapshot(s) into {cache_dir / 'solar_system' / 'bright_object_planner.sqlite3'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())