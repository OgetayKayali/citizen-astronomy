from __future__ import annotations

import sys
from pathlib import Path


APP_DISPLAY_NAME = "Citizen Astronomy (CAst)"
APP_WINDOW_TITLE_NAME = "Citizen Astronomy"
APP_USER_MODEL_ID = "CitizenAstronomy.CAst"
APP_VERSION = "0.1.1-alpha.5"
APP_UPDATE_CHANNEL = "alpha"
# Set this to the public "owner/repository" before publishing the first update.
APP_UPDATE_GITHUB_REPOSITORY = "OgetayKayali/citizen-astronomy"
APP_UPDATE_MANIFEST_ASSET_NAME = "CitizenAstronomy-update.json"

_APP_ICON_CANDIDATE_NAMES = (
    "citizen_astronomy.ico",
    "app_icon.ico",
    "citizen_astronomy.png",
    "app_icon.png",
    "citizen_astronomy.svg",
    "app_icon.svg",
)


def application_root_path() -> Path:
    bundle_root = getattr(sys, "_MEIPASS", None)
    if bundle_root:
        return Path(bundle_root)
    return Path(__file__).resolve().parent.parent


def application_install_path() -> Path:
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parent.parent


def application_icon_path() -> Path | None:
    assets_dir = application_root_path() / "assets"
    for icon_name in _APP_ICON_CANDIDATE_NAMES:
        candidate = assets_dir / icon_name
        if candidate.is_file():
            return candidate
    return None