from __future__ import annotations

import os
import sys
from pathlib import Path


_LINUX_DIRECTORY_NAME = "citizen-astronomy"
_WINDOWS_DIRECTORY_NAME = "CitizenPhotometry"


def application_config_dir() -> Path:
    if sys.platform.startswith("linux"):
        return _xdg_directory("XDG_CONFIG_HOME", Path.home() / ".config")
    return _legacy_application_dir()


def application_state_dir() -> Path:
    if sys.platform.startswith("linux"):
        return _xdg_directory("XDG_STATE_HOME", Path.home() / ".local" / "state")
    return _legacy_application_dir()


def application_data_dir() -> Path:
    if sys.platform.startswith("linux"):
        return _xdg_directory("XDG_DATA_HOME", Path.home() / ".local" / "share")
    return _legacy_application_dir()


def application_cache_dir() -> Path:
    if sys.platform.startswith("linux"):
        return _xdg_directory("XDG_CACHE_HOME", Path.home() / ".cache")

    local_app_data = os.getenv("LOCALAPPDATA")
    if local_app_data:
        return Path(local_app_data) / _WINDOWS_DIRECTORY_NAME
    return Path.home() / ".cache" / _WINDOWS_DIRECTORY_NAME


def application_log_dir() -> Path:
    if sys.platform.startswith("linux"):
        return application_state_dir()

    local_app_data = os.getenv("LOCALAPPDATA")
    if local_app_data:
        return Path(local_app_data) / "CitizenAstronomy"
    return Path.home() / ".citizen-astronomy"


def _xdg_directory(environment_variable: str, fallback_root: Path) -> Path:
    configured_root = os.getenv(environment_variable)
    root = Path(configured_root).expanduser() if configured_root else fallback_root
    if not root.is_absolute():
        root = fallback_root
    return root / _LINUX_DIRECTORY_NAME


def _legacy_application_dir() -> Path:
    local_app_data = os.getenv("LOCALAPPDATA")
    if local_app_data:
        return Path(local_app_data) / _WINDOWS_DIRECTORY_NAME
    return Path.home() / ".citizen-photometry"


__all__ = [
    "application_cache_dir",
    "application_config_dir",
    "application_data_dir",
    "application_log_dir",
    "application_state_dir",
]
