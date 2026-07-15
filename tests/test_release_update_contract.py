from __future__ import annotations

from pathlib import Path
import re
import tomllib
import unittest

from photometry_app.app_metadata import (
    APP_USER_MODEL_ID,
    APP_VERSION,
)


ROOT = Path(__file__).resolve().parents[1]
INNO_PATH = ROOT / "packaging" / "inno" / "CitizenAstronomyAlphaReview.iss"
PUBLISHER_PATH = ROOT / "packaging" / "publish_github_update.ps1"
TWO_VERSION_VALIDATOR_PATH = ROOT / "packaging" / "validate_two_version_update.ps1"


def _pep440_version(semantic_version: str) -> str:
    match = re.fullmatch(
        r"(?P<core>\d+\.\d+\.\d+)"
        r"(?:-(?P<label>alpha|beta|rc)(?:\.(?P<number>\d+))?)?",
        semantic_version,
    )
    if match is None:
        raise AssertionError(f"Unsupported application version in contract test: {semantic_version}")
    label = match.group("label")
    if label is None:
        return match.group("core")
    abbreviation = {"alpha": "a", "beta": "b", "rc": "rc"}[label]
    return f"{match.group('core')}{abbreviation}{match.group('number') or '0'}"


class ReleaseUpdateContractTest(unittest.TestCase):
    def test_runtime_package_and_inno_versions_are_synchronized(self) -> None:
        project = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))
        inno_source = INNO_PATH.read_text(encoding="utf-8")

        self.assertEqual(project["project"]["version"], _pep440_version(APP_VERSION))
        self.assertIn(f'#define AppVersion "{APP_VERSION}"', inno_source)

    def test_inno_update_mode_preserves_identity_and_relaunches(self) -> None:
        source = INNO_PATH.read_text(encoding="utf-8")

        self.assertIn("AppId={{A4D6F2B1-7C93-4E2A-9B61-3F8E5D0C1A72}", source)
        self.assertIn(
            r"DefaultDirName={localappdata}\Programs\Citizen Astronomy (CAst) Alpha Review",
            source,
        )
        self.assertIn("CloseApplications=force", source)
        self.assertIn("RestartApplications=no", source)
        self.assertIn("'/UPDATE=1'", source)
        self.assertIn("Flags: nowait runasoriginaluser; Check: IsUpdateInstall", source)

    def test_publisher_uses_embedded_manifest_contract_and_clean_source(self) -> None:
        source = PUBLISHER_PATH.read_text(encoding="utf-8")

        self.assertIn('Get-PythonStringConstant -Source $metadataSource -Name "APP_VERSION"', source)
        self.assertIn("-Version '$Version' does not match the version embedded", source)
        self.assertIn('"status", "--porcelain=v1", "--untracked-files=all"', source)
        self.assertIn(
            'Get-PythonStringConstant -Source $metadataSource -Name "APP_UPDATE_MANIFEST_ASSET_NAME"',
            source,
        )
        for field_name in (
            "schema_version",
            "app_id",
            "channel",
            "version",
            "installer_asset",
            "installer_size",
            "installer_sha256",
            "notes",
        ):
            self.assertIn(f'"{field_name}"', source)
        self.assertIn('"release", "create", $tag', source)

    def test_two_version_validator_checks_required_install_outcomes(self) -> None:
        source = TWO_VERSION_VALIDATOR_PATH.read_text(encoding="utf-8")

        self.assertIn("-not $ConfirmCleanTestEnvironment", source)
        self.assertIn('"/UPDATE=1"', source)
        self.assertIn("updater_validation_sentinel", source)
        self.assertIn("newer_process_relaunched", source)
        self.assertIn("start_menu_shortcut_valid", source)
        self.assertIn("uninstall_registration_valid", source)
        self.assertIn("uninstall_display_version", source)
        self.assertIn(APP_USER_MODEL_ID.split(".")[0], source)


if __name__ == "__main__":
    unittest.main()
