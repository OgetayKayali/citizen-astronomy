from __future__ import annotations

from types import SimpleNamespace
import unittest
from unittest.mock import MagicMock, patch

from photometry_app.core import app_updates
from photometry_app.core.app_updates import (
    AvailableUpdate,
    DownloadedUpdate,
    UpdateConfigurationError,
    UpdateDownloadCancelled,
    UpdateNetworkError,
    UpdateVerificationError,
    apply_update_and_restart,
    check_for_updates,
    download_update_package,
)
from photometry_app.app_metadata import application_update_channel


_SHA256 = "a" * 64


def _asset(
    *,
    version: str = "0.1.1-alpha.4",
    asset_type: str = "Full",
    filename: str = "CitizenAstronomy.CAst-0.1.1-alpha.4-full.nupkg",
    size: int = 10_000,
    sha256: str = _SHA256,
    notes: str = "Reviewer hotfix",
) -> SimpleNamespace:
    return SimpleNamespace(
        Version=version,
        Type=asset_type,
        FileName=filename,
        SHA256=sha256,
        Size=size,
        NotesMarkdown=notes,
        NotesHtml=f"<p>{notes}</p>",
    )


def _update_info(
    *,
    target: SimpleNamespace | None = None,
    deltas: list[SimpleNamespace] | None = None,
) -> SimpleNamespace:
    return SimpleNamespace(
        TargetFullRelease=target or _asset(),
        DeltasToTarget=list(deltas or []),
        IsDowngrade=False,
    )


class FakeManager:
    def __init__(
        self,
        update_info: SimpleNamespace | None = None,
        *,
        current_version: str = "0.1.1-alpha.3",
    ) -> None:
        self.update_info = update_info
        self.current_version = current_version
        self.downloaded: list[object] = []
        self.applied: list[object] = []
        self.progress_values = [0, 50, 100]

    def get_current_version(self) -> str:
        return self.current_version

    def get_is_portable(self) -> bool:
        return False

    def check_for_updates(self) -> SimpleNamespace | None:
        return self.update_info

    def download_updates(self, update_info: object, progress_callback=None) -> None:
        self.downloaded.append(update_info)
        if progress_callback is not None:
            for value in self.progress_values:
                progress_callback(value)

    def apply_updates_and_restart(self, update_info: object) -> None:
        self.applied.append(update_info)


class VelopackUpdateCheckTest(unittest.TestCase):
    def test_no_update_reports_managed_current_version(self) -> None:
        manager = FakeManager(None, current_version="0.1.1-alpha.3")

        result = check_for_updates(manager_factory=lambda: manager)

        self.assertEqual(result.current_version, "0.1.1-alpha.3")
        self.assertEqual(result.channel, application_update_channel())
        self.assertFalse(result.update_available)
        self.assertIsNone(result.available_update)

    def test_delta_chain_reports_actual_download_size(self) -> None:
        info = _update_info(
            target=_asset(size=80_000, notes="Small pipeline fix"),
            deltas=[
                _asset(asset_type="Delta", filename="one-delta.nupkg", size=1_200),
                _asset(asset_type="Delta", filename="two-delta.nupkg", size=800),
            ],
        )

        result = check_for_updates(manager_factory=lambda: FakeManager(info))

        update = result.available_update
        self.assertIsNotNone(update)
        assert update is not None
        self.assertEqual(update.version, "0.1.1-alpha.4")
        self.assertEqual(update.notes, "Small pipeline fix")
        self.assertEqual(update.download_size, 2_000)
        self.assertEqual(update.full_package_size, 80_000)
        self.assertEqual(update.delta_count, 2)
        self.assertTrue(update.is_delta)
        self.assertEqual(update.package_kind, "delta")

    def test_full_fallback_reports_full_package(self) -> None:
        info = _update_info(target=_asset(size=45_000), deltas=[])

        update = check_for_updates(
            manager_factory=lambda: FakeManager(info)
        ).available_update

        self.assertIsNotNone(update)
        assert update is not None
        self.assertFalse(update.is_delta)
        self.assertEqual(update.package_kind, "full")
        self.assertEqual(update.download_size, 45_000)

    def test_invalid_package_metadata_is_rejected_before_download(self) -> None:
        info = _update_info(target=_asset(sha256="not-a-digest"))

        with self.assertRaisesRegex(UpdateVerificationError, "SHA-256"):
            check_for_updates(manager_factory=lambda: FakeManager(info))

    def test_unmanaged_install_has_actionable_message(self) -> None:
        def fail() -> FakeManager:
            raise RuntimeError(
                "This application is not properly installed: "
                "Could not auto-locate app manifest"
            )

        with self.assertRaisesRegex(
            UpdateConfigurationError,
            "Velopack-managed",
        ):
            check_for_updates(manager_factory=fail)

    def test_manager_uses_public_github_prereleases_and_alpha_channel(self) -> None:
        source = object()
        manager = object()
        with (
            patch("velopack.GithubSource", return_value=source) as source_type,
            patch("velopack.UpdateOptions", return_value="options") as options_type,
            patch("velopack.UpdateManager", return_value=manager) as manager_type,
        ):
            created = app_updates._create_update_manager()

        self.assertIs(created, manager)
        source_type.assert_called_once_with(
            "https://github.com/OgetayKayali/citizen-astronomy",
            access_token=None,
            prerelease=True,
        )
        options_type.assert_called_once_with(False, 10, application_update_channel())
        manager_type.assert_called_once_with(source, "options")

    def test_check_failure_is_presented_as_network_error(self) -> None:
        manager = FakeManager()
        manager.check_for_updates = MagicMock(
            side_effect=RuntimeError("connection timed out")
        )

        with self.assertRaisesRegex(UpdateNetworkError, "timed out"):
            check_for_updates(manager_factory=lambda: manager)


class VelopackDownloadApplyTest(unittest.TestCase):
    def setUp(self) -> None:
        self.info = _update_info(
            target=_asset(size=10_000),
            deltas=[_asset(asset_type="Delta", size=1_000)],
        )
        checked = check_for_updates(
            manager_factory=lambda: FakeManager(self.info)
        )
        assert checked.available_update is not None
        self.update: AvailableUpdate = checked.available_update

    def test_download_forwards_byte_progress_and_returns_staged_update(self) -> None:
        manager = FakeManager()
        progress: list[tuple[int, int]] = []

        downloaded = download_update_package(
            self.update,
            progress_callback=lambda done, total: progress.append((done, total)),
            manager_factory=lambda: manager,
        )

        self.assertEqual(manager.downloaded, [self.info])
        self.assertIsInstance(downloaded, DownloadedUpdate)
        self.assertEqual(downloaded.update, self.update)
        self.assertEqual(
            progress,
            [(0, 1_000), (500, 1_000), (1_000, 1_000), (1_000, 1_000)],
        )

    def test_download_can_be_cancelled_from_progress_callback(self) -> None:
        manager = FakeManager()
        cancel = False

        def progress(_done: int, _total: int) -> None:
            nonlocal cancel
            cancel = True

        with self.assertRaisesRegex(UpdateDownloadCancelled, "canceled"):
            download_update_package(
                self.update,
                progress_callback=progress,
                cancellation_requested=lambda: cancel,
                manager_factory=lambda: manager,
            )

    def test_corrupt_download_is_reported_as_verification_failure(self) -> None:
        manager = FakeManager()
        manager.download_updates = MagicMock(
            side_effect=RuntimeError("package checksum verification failed")
        )

        with self.assertRaisesRegex(UpdateVerificationError, "verified"):
            download_update_package(
                self.update,
                manager_factory=lambda: manager,
            )

    def test_apply_delegates_atomic_restart_to_velopack(self) -> None:
        manager = FakeManager()
        downloaded = DownloadedUpdate(self.update)

        apply_update_and_restart(
            downloaded,
            manager_factory=lambda: manager,
        )

        self.assertEqual(manager.applied, [self.info])

    def test_public_functions_reject_wrong_object_types(self) -> None:
        with self.assertRaises(TypeError):
            download_update_package(object())  # type: ignore[arg-type]
        with self.assertRaises(TypeError):
            apply_update_and_restart(object())  # type: ignore[arg-type]


if __name__ == "__main__":
    unittest.main()
