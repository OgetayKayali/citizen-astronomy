from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import time
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_EXE_CANDIDATES = (
    ROOT / "_tmp_alpha_review_dist2" / "CitizenAstronomyAlphaReview" / "CitizenAstronomyAlphaReview.exe",
    ROOT / "_tmp_alpha_review_dist" / "CitizenAstronomyAlphaReview" / "CitizenAstronomyAlphaReview.exe",
)
DEFAULT_EXE = next((candidate for candidate in DEFAULT_EXE_CANDIDATES if candidate.is_file()), DEFAULT_EXE_CANDIDATES[0])
DEFAULT_FIXTURES = ROOT / "packaging" / "fixtures"


def _startup_error_log_path() -> Path:
    local_app_data = os.getenv("LOCALAPPDATA")
    if local_app_data:
        return Path(local_app_data) / "CitizenAstronomy" / "startup-error.log"
    return Path.home() / ".citizen-astronomy" / "startup-error.log"


def _kill_previous_instances(exe_name: str) -> None:
    helper_pid = os.getpid()
    try:
        import psutil
    except ImportError:
        return

    for process in psutil.process_iter(["pid", "name", "cmdline"]):
        try:
            if process.info["pid"] in {helper_pid, os.getpid()}:
                continue
            command_line = " ".join(process.info.get("cmdline") or [])
            process_name = str(process.info.get("name") or "")
            if exe_name.casefold() not in command_line.casefold() and exe_name.casefold() not in process_name.casefold():
                continue
            process.terminate()
        except Exception:
            continue


def _run_startup_smoke(exe_path: Path) -> dict[str, object]:
    _kill_previous_instances(exe_path.name)
    startup_log = _startup_error_log_path()
    log_existed_before = startup_log.exists()
    if log_existed_before:
        startup_log.unlink()

    stdout_path = ROOT / "_tmp_alpha_review_packaged_startup_smoke_out.txt"
    stderr_path = ROOT / "_tmp_alpha_review_packaged_startup_smoke_err.txt"
    for path in (stdout_path, stderr_path):
        if path.exists():
            path.unlink()

    with stdout_path.open("w", encoding="utf-8") as stdout_handle, stderr_path.open("w", encoding="utf-8") as stderr_handle:
        process = subprocess.Popen(
            [str(exe_path)],
            cwd=str(exe_path.parent),
            stdout=stdout_handle,
            stderr=stderr_handle,
        )
        time.sleep(10)
        exit_code = process.poll()

    if exit_code is None:
        try:
            process.terminate()
            process.wait(timeout=5)
        except Exception:
            try:
                process.kill()
            except Exception:
                pass

    return {
        "success": exit_code is None and not startup_log.exists(),
        "status": "startup_ok" if exit_code is None else "startup_failed",
        "exit_code": exit_code,
        "startup_error_log_created": startup_log.exists(),
        "startup_error_log": str(startup_log),
    }


def _run_cli_smoke(exe_path: Path, args: list[str]) -> tuple[int, str]:
    completed = subprocess.run(
        [str(exe_path), *args],
        cwd=str(exe_path.parent),
        capture_output=True,
        text=True,
        check=False,
    )
    output = completed.stdout.strip() or completed.stderr.strip()
    return completed.returncode, output


def run_packaged_alpha_smoke(
    *,
    exe_path: Path,
    fixtures_dir: Path,
    output_path: Path,
) -> dict[str, object]:
    if not exe_path.is_file():
        raise FileNotFoundError(f"Packaged executable not found: {exe_path}")
    if not fixtures_dir.is_dir():
        raise FileNotFoundError(f"Smoke fixtures directory not found: {fixtures_dir}")

    startup_result = _run_startup_smoke(exe_path)

    format_output = (output_path.parent / "_tmp_packaged_format_smoke_result.json").resolve()
    if format_output.exists():
        format_output.unlink()

    format_code, format_stdout = _run_cli_smoke(
        exe_path,
        [
            "--packaged-format-smoke",
            "--packaged-format-smoke-fixtures",
            str(fixtures_dir.resolve()),
            "--packaged-format-smoke-output",
            str(format_output),
        ],
    )
    for _attempt in range(20):
        if format_output.is_file():
            break
        time.sleep(0.25)

    if format_output.is_file():
        format_result = json.loads(format_output.read_text(encoding="utf-8"))
    elif format_stdout:
        format_result = json.loads(format_stdout)
    else:
        format_result = {}

    about_code, about_stdout = _run_cli_smoke(exe_path, ["--about-dialog-smoke"])
    try:
        about_result = json.loads(about_stdout) if about_stdout else {"success": about_code == 0, "error": ""}
    except json.JSONDecodeError:
        about_result = {"success": about_code == 0, "error": ""}

    success = bool(
        startup_result.get("success")
        and bool(format_result.get("success"))
        and bool(about_result.get("success"))
    )

    combined = {
        "success": success,
        "exe_path": str(exe_path),
        "fixtures_dir": str(fixtures_dir),
        "startup": startup_result,
        "format_smoke_exit_code": format_code,
        "format": format_result,
        "about_dialog_exit_code": about_code,
        "about_dialog": about_result,
    }
    output_path.write_text(json.dumps(combined, indent=2), encoding="utf-8")
    return combined


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Run packaged alpha-review smoke checks against the frozen executable.")
    parser.add_argument("--exe", default=str(DEFAULT_EXE))
    parser.add_argument("--fixtures", default=str(DEFAULT_FIXTURES))
    parser.add_argument("--output", default=str(ROOT / "_tmp_packaged_alpha_smoke_result.json"))
    args = parser.parse_args(argv)

    result = run_packaged_alpha_smoke(
        exe_path=Path(args.exe),
        fixtures_dir=Path(args.fixtures),
        output_path=Path(args.output),
    )
    print(json.dumps(result, indent=2))
    return 0 if bool(result.get("success")) else 1


if __name__ == "__main__":
    raise SystemExit(main())
