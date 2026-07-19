from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
import re
from typing import NoReturn, Protocol

from photometry_app.app_metadata import (
    APP_UPDATE_CHANNEL,
    APP_UPDATE_GITHUB_REPOSITORY,
    APP_VERSION,
)


_REPOSITORY_PATTERN = re.compile(
    r"^[A-Za-z0-9](?:[A-Za-z0-9-]{0,38})/[A-Za-z0-9._-]+$"
)
_SHA256_PATTERN = re.compile(r"^[0-9a-fA-F]{64}$")
_MAXIMUM_DELTAS_BEFORE_FULL_FALLBACK = 10


class AppUpdateError(RuntimeError):
    """Base class for update errors that can be presented in the UI."""


class UpdateConfigurationError(AppUpdateError):
    """The running application cannot use its configured update source."""


class UpdateNetworkError(AppUpdateError):
    """The update feed or package could not be reached."""


class UpdateVerificationError(AppUpdateError):
    """A downloaded package failed Velopack integrity verification."""


class UpdateDownloadCancelled(AppUpdateError):
    """The caller cancelled an update package download."""


# Alternate spelling for callers that use American English.
UpdateDownloadCanceled = UpdateDownloadCancelled


class _VelopackAsset(Protocol):
    Version: str
    Type: str
    FileName: str
    SHA256: str
    Size: int
    NotesMarkdown: str
    NotesHtml: str


class _VelopackUpdateInfo(Protocol):
    TargetFullRelease: _VelopackAsset
    DeltasToTarget: list[_VelopackAsset]
    IsDowngrade: bool


class _VelopackManager(Protocol):
    def check_for_updates(self) -> _VelopackUpdateInfo | None: ...

    def download_updates(
        self,
        update_info: _VelopackUpdateInfo,
        progress_callback: Callable[[int], None] | None = None,
    ) -> None: ...

    def apply_updates_and_restart(self, update: _VelopackUpdateInfo) -> None: ...

    def get_current_version(self) -> object: ...

    def get_is_portable(self) -> bool: ...


@dataclass(frozen=True, slots=True)
class AvailableUpdate:
    """A Velopack-selected update path and display metadata."""

    version: str
    notes: str
    download_size: int
    full_package_size: int
    package_kind: str
    delta_count: int
    _update_info: _VelopackUpdateInfo = field(repr=False, compare=False)

    @property
    def is_delta(self) -> bool:
        return self.package_kind == "delta"

    @property
    def installer_size(self) -> int:
        """Compatibility alias for older UI/tests during the migration."""

        return self.download_size


@dataclass(frozen=True, slots=True)
class UpdateCheckResult:
    current_version: str
    channel: str
    available_update: AvailableUpdate | None

    @property
    def update_available(self) -> bool:
        return self.available_update is not None

    @property
    def update(self) -> AvailableUpdate | None:
        return self.available_update


@dataclass(frozen=True, slots=True)
class DownloadedUpdate:
    """A verified update that Velopack has staged for application."""

    update: AvailableUpdate


UpdateManagerFactory = Callable[[], _VelopackManager]


def _repository_url(repository: str) -> str:
    value = str(repository or "").strip()
    if not _REPOSITORY_PATTERN.fullmatch(value):
        raise UpdateConfigurationError(
            "The update repository must be configured as a public owner/repository name."
        )
    return f"https://github.com/{value}"


def _create_update_manager(
    *,
    repository: str = APP_UPDATE_GITHUB_REPOSITORY,
    channel: str = APP_UPDATE_CHANNEL,
) -> _VelopackManager:
    try:
        from velopack import GithubSource, UpdateManager, UpdateOptions
    except ImportError as exc:
        raise UpdateConfigurationError(
            "The Velopack runtime is missing from this Citizen Astronomy build."
        ) from exc

    normalized_channel = str(channel or "").strip().lower()
    if not normalized_channel:
        raise UpdateConfigurationError("The application update channel is not configured.")

    source = GithubSource(
        _repository_url(repository),
        access_token=None,
        prerelease=normalized_channel != "stable",
    )
    options = UpdateOptions(
        False,
        _MAXIMUM_DELTAS_BEFORE_FULL_FALLBACK,
        normalized_channel,
    )
    try:
        return UpdateManager(source, options)
    except Exception as exc:
        _raise_update_error(exc, operation="initialize")


def check_for_updates(
    *,
    manager_factory: UpdateManagerFactory | None = None,
) -> UpdateCheckResult:
    """Check the configured GitHub release channel through Velopack."""

    try:
        manager = (manager_factory or _create_update_manager)()
    except AppUpdateError:
        raise
    except Exception as exc:
        _raise_update_error(exc, operation="initialize")
    try:
        current_version = str(manager.get_current_version() or APP_VERSION)
        update_info = manager.check_for_updates()
    except Exception as exc:
        _raise_update_error(exc, operation="check")

    if update_info is None:
        return UpdateCheckResult(
            current_version=current_version,
            channel=APP_UPDATE_CHANNEL,
            available_update=None,
        )

    return UpdateCheckResult(
        current_version=current_version,
        channel=APP_UPDATE_CHANNEL,
        available_update=_available_update(update_info),
    )


def _available_update(update_info: _VelopackUpdateInfo) -> AvailableUpdate:
    target = update_info.TargetFullRelease
    version = str(target.Version or "").strip()
    if not version:
        raise UpdateVerificationError("The Velopack update has no target version.")

    full_size = _validated_asset_size(target, "full update package")
    _validate_asset_sha256(target, "full update package")

    deltas = list(update_info.DeltasToTarget or [])
    for delta in deltas:
        _validated_asset_size(delta, "delta update package")
        _validate_asset_sha256(delta, "delta update package")

    download_size = sum(int(delta.Size) for delta in deltas) if deltas else full_size
    notes = str(target.NotesMarkdown or "").strip()
    return AvailableUpdate(
        version=version,
        notes=notes,
        download_size=download_size,
        full_package_size=full_size,
        package_kind="delta" if deltas else "full",
        delta_count=len(deltas),
        _update_info=update_info,
    )


def _validated_asset_size(asset: _VelopackAsset, label: str) -> int:
    try:
        size = int(asset.Size)
    except (TypeError, ValueError) as exc:
        raise UpdateVerificationError(f"The {label} has an invalid size.") from exc
    if size <= 0:
        raise UpdateVerificationError(f"The {label} has an invalid size.")
    return size


def _validate_asset_sha256(asset: _VelopackAsset, label: str) -> None:
    digest = str(asset.SHA256 or "").strip()
    if not _SHA256_PATTERN.fullmatch(digest):
        raise UpdateVerificationError(f"The {label} has an invalid SHA-256 digest.")


def download_update_package(
    update: AvailableUpdate,
    *,
    progress_callback: Callable[[int, int], None] | None = None,
    cancellation_requested: Callable[[], bool] | None = None,
    manager_factory: UpdateManagerFactory | None = None,
) -> DownloadedUpdate:
    """Download and verify the Velopack-selected delta chain or full package."""

    if not isinstance(update, AvailableUpdate):
        raise TypeError("update must be an AvailableUpdate.")

    total_bytes = max(0, int(update.download_size))

    def _progress(percent: int) -> None:
        if cancellation_requested is not None and cancellation_requested():
            raise UpdateDownloadCancelled("Update download canceled.")
        bounded_percent = max(0, min(100, int(percent)))
        downloaded = int(round(total_bytes * bounded_percent / 100.0))
        if progress_callback is not None:
            progress_callback(downloaded, total_bytes)

    if cancellation_requested is not None and cancellation_requested():
        raise UpdateDownloadCancelled("Update download canceled.")

    try:
        manager = (manager_factory or _create_update_manager)()
    except AppUpdateError:
        raise
    except Exception as exc:
        _raise_update_error(exc, operation="initialize")
    try:
        manager.download_updates(update._update_info, _progress)
    except UpdateDownloadCancelled:
        raise
    except Exception as exc:
        _raise_update_error(exc, operation="download")

    if progress_callback is not None:
        progress_callback(total_bytes, total_bytes)
    return DownloadedUpdate(update=update)


def apply_update_and_restart(
    downloaded_update: DownloadedUpdate,
    *,
    manager_factory: UpdateManagerFactory | None = None,
) -> None:
    """Ask Velopack's external updater to atomically apply and restart."""

    if not isinstance(downloaded_update, DownloadedUpdate):
        raise TypeError("downloaded_update must be a DownloadedUpdate.")
    try:
        manager = (manager_factory or _create_update_manager)()
    except AppUpdateError:
        raise
    except Exception as exc:
        _raise_update_error(exc, operation="initialize")
    try:
        manager.apply_updates_and_restart(downloaded_update.update._update_info)
    except Exception as exc:
        _raise_update_error(exc, operation="apply")


def _raise_update_error(exc: Exception, *, operation: str) -> NoReturn:
    message = str(exc).strip() or exc.__class__.__name__
    folded = message.casefold()
    if "not properly installed" in folded or "auto-locate app manifest" in folded:
        raise UpdateConfigurationError(
            "Updates can only be installed from a Velopack-managed Citizen Astronomy "
            "installation. Install the current Setup executable first."
        ) from exc
    if any(
        token in folded
        for token in ("checksum", "sha", "hash", "corrupt", "verification", "signature")
    ):
        raise UpdateVerificationError(
            f"The downloaded update could not be verified: {message}"
        ) from exc
    if operation in {"check", "download"}:
        raise UpdateNetworkError(f"Could not {operation} for updates: {message}") from exc
    raise AppUpdateError(f"Could not {operation} the update: {message}") from exc


__all__ = [
    "AppUpdateError",
    "AvailableUpdate",
    "DownloadedUpdate",
    "UpdateCheckResult",
    "UpdateConfigurationError",
    "UpdateDownloadCanceled",
    "UpdateDownloadCancelled",
    "UpdateNetworkError",
    "UpdateVerificationError",
    "apply_update_and_restart",
    "check_for_updates",
    "download_update_package",
]
