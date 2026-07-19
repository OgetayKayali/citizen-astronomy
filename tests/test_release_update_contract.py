from __future__ import annotations

from pathlib import Path
import re
import tomllib
import unittest

from photometry_app.app_metadata import APP_USER_MODEL_ID, APP_VERSION


ROOT = Path(__file__).resolve().parents[1]
SPEC_PATH = ROOT / "CitizenAstronomyAlphaReview.spec"
MAIN_PATH = ROOT / "photometry_app" / "main.py"
BOOTSTRAP_INNO_PATH = (
    ROOT / "packaging" / "inno" / "CitizenAstronomyVelopackBootstrap.iss"
)
PUBLISHER_PATH = ROOT / "packaging" / "publish_github_update.ps1"
THREE_STAGE_VALIDATOR_PATH = (
    ROOT / "packaging" / "validate_two_version_update.ps1"
)


def _pep440_version(semantic_version: str) -> str:
    match = re.fullmatch(
        r"(?P<core>\d+\.\d+\.\d+)"
        r"(?:-(?P<label>alpha|beta|rc)(?:\.(?P<number>\d+))?)?",
        semantic_version,
    )
    if match is None:
        raise AssertionError(
            f"Unsupported application version in contract test: {semantic_version}"
        )
    label = match.group("label")
    if label is None:
        return match.group("core")
    abbreviation = {"alpha": "a", "beta": "b", "rc": "rc"}[label]
    return f"{match.group('core')}{abbreviation}{match.group('number') or '0'}"


class ReleaseUpdateContractTest(unittest.TestCase):
    def test_runtime_package_and_velopack_versions_are_synchronized(self) -> None:
        project = tomllib.loads(
            (ROOT / "pyproject.toml").read_text(encoding="utf-8")
        )
        dependencies = project["project"]["dependencies"]
        publisher_source = PUBLISHER_PATH.read_text(encoding="utf-8")

        self.assertEqual(project["project"]["version"], _pep440_version(APP_VERSION))
        self.assertIn("velopack==1.2.0", dependencies)
        self.assertIn('$velopackToolVersion = "1.2.0"', publisher_source)

    def test_frozen_bundle_is_delta_friendly_and_contains_velopack(self) -> None:
        spec_source = SPEC_PATH.read_text(encoding="utf-8")
        main_source = MAIN_PATH.read_text(encoding="utf-8")

        self.assertIn('"velopack"', spec_source)
        self.assertNotIn("upx=True", spec_source)
        self.assertGreaterEqual(spec_source.count("upx=False"), 2)
        self.assertIn("App().set_auto_apply_on_startup(True).run()", main_source)
        self.assertLess(
            main_source.index("_run_velopack_startup()"),
            main_source.index("_parse_cli_args(sys.argv)"),
        )

    def test_publisher_builds_signed_full_delta_and_alpha_feed(self) -> None:
        source = PUBLISHER_PATH.read_text(encoding="utf-8")

        self.assertIn(
            'Get-PythonStringConstant -Source $metadataSource -Name "APP_VERSION"',
            source,
        )
        self.assertIn("-Version '$Version' does not match the version embedded", source)
        self.assertIn('"status", "--porcelain=v1", "--untracked-files=all"', source)
        self.assertIn('"download", "github"', source)
        self.assertIn('"pack"', source)
        self.assertIn('"--delta", "BestSize"', source)
        self.assertIn('"--noPortable", "true"', source)
        self.assertIn('"upload", "github"', source)
        self.assertIn('"--publish", "true"', source)
        self.assertIn('"--pre", "true"', source)
        self.assertIn('"releases.$channel.json"', source)
        self.assertIn("*-full.nupkg", source)
        self.assertIn("*-delta.nupkg", source)
        self.assertIn("Code signing is required.", source)
        self.assertIn("$githubReleaseAssetLimitBytes = 2GB", source)
        self.assertIn("CITIZEN_ASTRONOMY_SIGN_TEMPLATE", source)
        self.assertIn("CITIZEN_ASTRONOMY_AZURE_SIGN_FILE", source)

    def test_first_release_includes_legacy_schema_v1_bootstrap(self) -> None:
        publisher = PUBLISHER_PATH.read_text(encoding="utf-8")
        bootstrap = BOOTSTRAP_INNO_PATH.read_text(encoding="utf-8")

        self.assertIn("FirstVelopackRelease", publisher)
        self.assertIn("IncludeLegacyBootstrap", publisher)
        self.assertIn("CitizenAstronomyVelopackBootstrap.iss", publisher)
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
            self.assertIn(field_name, publisher)
        self.assertIn("--silent", bootstrap)
        self.assertIn("CreateUninstallRegKey=no", bootstrap)
        self.assertIn("Uninstallable=no", bootstrap)
        self.assertIn("unins000.exe", bootstrap)
        self.assertIn(r"{localappdata}\CitizenAstronomy.CAst", bootstrap)
        self.assertIn("CreateShellLink", bootstrap)
        self.assertIn("The existing Citizen", bootstrap)
        self.assertIn("Astronomy installation was left unchanged", bootstrap)

    def test_three_stage_validator_checks_migration_and_delta_outcomes(self) -> None:
        source = THREE_STAGE_VALIDATOR_PATH.read_text(encoding="utf-8")

        self.assertIn("-not $ConfirmCleanTestEnvironment", source)
        for parameter in (
            "LegacyInstaller",
            "BootstrapInstaller",
            "NextFullPackage",
            "NextDeltaPackage",
        ):
            self.assertIn(parameter, source)
        self.assertIn('"patch"', source)
        self.assertIn('"--delta", $NextDeltaPackage', source)
        self.assertIn("Get-FileHash", source)
        self.assertIn('"apply"', source)
        self.assertIn("bootstrap_migration_succeeded", source)
        self.assertIn("delta_reconstructed", source)
        self.assertIn("delta_matches_full_sha256", source)
        self.assertIn("corrupt_delta_rejected", source)
        self.assertIn("working_version_preserved_on_error", source)
        self.assertIn("delta_applied", source)
        self.assertIn("setting_retained", source)
        self.assertIn("start_menu_shortcut_valid", source)
        self.assertIn("uninstall_registration_valid", source)
        self.assertIn(APP_USER_MODEL_ID, source)


if __name__ == "__main__":
    unittest.main()
