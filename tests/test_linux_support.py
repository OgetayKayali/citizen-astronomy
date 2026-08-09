from __future__ import annotations

import os
from pathlib import Path
from unittest.mock import patch

from photometry_app import main
from photometry_app.app_metadata import application_update_channel
from photometry_app.core import candidate_training, settings, solar_system
from photometry_app.platform_paths import (
    application_cache_dir,
    application_config_dir,
    application_data_dir,
    application_log_dir,
    application_state_dir,
)


def test_linux_uses_separate_update_channel() -> None:
    assert application_update_channel("linux") == "alpha-linux"
    assert application_update_channel("win32") == "alpha"


def test_linux_uses_xdg_base_directories() -> None:
    environment = {
        "XDG_CONFIG_HOME": "/tmp/cast-config",
        "XDG_STATE_HOME": "/tmp/cast-state",
        "XDG_DATA_HOME": "/tmp/cast-data",
        "XDG_CACHE_HOME": "/tmp/cast-cache",
    }
    with patch("photometry_app.platform_paths.sys.platform", "linux"):
        with patch.dict(os.environ, environment, clear=True):
            assert application_config_dir() == Path("/tmp/cast-config/citizen-astronomy")
            assert application_state_dir() == Path("/tmp/cast-state/citizen-astronomy")
            assert application_data_dir() == Path("/tmp/cast-data/citizen-astronomy")
            assert application_cache_dir() == Path("/tmp/cast-cache/citizen-astronomy")
            assert application_log_dir() == Path("/tmp/cast-state/citizen-astronomy")
            assert settings.default_settings_config_path() == Path(
                "/tmp/cast-config/citizen-astronomy/settings.json"
            )
            assert settings._app_state_path() == Path(
                "/tmp/cast-state/citizen-astronomy/state.json"
            )
            assert candidate_training.default_training_database_path() == Path(
                "/tmp/cast-data/citizen-astronomy/candidate-training.sqlite3"
            )
            assert solar_system._major_planet_cache_directory() == Path(
                "/tmp/cast-cache/citizen-astronomy/solar_system"
            )
            assert main._startup_log_path() == Path(
                "/tmp/cast-state/citizen-astronomy/startup-error.log"
            )


def test_relative_xdg_path_is_ignored() -> None:
    with patch("photometry_app.platform_paths.sys.platform", "linux"):
        with patch.dict(os.environ, {"XDG_CONFIG_HOME": "relative"}, clear=True):
            expected = Path.home() / ".config" / "citizen-astronomy"
            assert application_config_dir() == expected


def test_windows_paths_remain_backward_compatible() -> None:
    with patch("photometry_app.platform_paths.sys.platform", "win32"):
        with patch.dict(os.environ, {"LOCALAPPDATA": "C:/Users/Test/AppData/Local"}, clear=True):
            local_app_data = Path("C:/Users/Test/AppData/Local")
            assert application_config_dir() == local_app_data / "CitizenPhotometry"
            assert application_state_dir() == local_app_data / "CitizenPhotometry"
            assert application_data_dir() == local_app_data / "CitizenPhotometry"
            assert application_cache_dir() == local_app_data / "CitizenPhotometry"
            assert application_log_dir() == local_app_data / "CitizenAstronomy"
