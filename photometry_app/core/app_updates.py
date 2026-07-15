from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from functools import total_ordering
import hashlib
import json
import os
from pathlib import Path
import re
from typing import Any

import requests

from photometry_app.app_metadata import (
    APP_UPDATE_CHANNEL,
    APP_UPDATE_GITHUB_REPOSITORY,
    APP_UPDATE_MANIFEST_ASSET_NAME,
    APP_USER_MODEL_ID,
    APP_VERSION,
)


UPDATE_MANIFEST_SCHEMA_VERSION = 1
GITHUB_API_ROOT = "https://api.github.com"
REQUEST_TIMEOUT_SECONDS = (5.0, 30.0)
DOWNLOAD_TIMEOUT_SECONDS = (10.0, 120.0)
DOWNLOAD_CHUNK_SIZE = 1024 * 1024

_SEMANTIC_VERSION_PATTERN = re.compile(
    r"""
    ^v?
    (?P<major>0|[1-9]\d*)\.
    (?P<minor>0|[1-9]\d*)\.
    (?P<patch>0|[1-9]\d*)
    (?:-(?P<prerelease>
        [0-9A-Za-z-]+
        (?:\.[0-9A-Za-z-]+)*
    ))?
    (?:\+(?P<build>
        [0-9A-Za-z-]+(?:\.[0-9A-Za-z-]+)*
    ))?
    $
    """,
    re.VERBOSE,
)
_REPOSITORY_PATTERN = re.compile(
    r"^[A-Za-z0-9](?:[A-Za-z0-9-]{0,38})/[A-Za-z0-9._-]+$"
)
_CHANNEL_PATTERN = re.compile(r"^[a-z0-9](?:[a-z0-9.-]*[a-z0-9])?$")
_SHA256_PATTERN = re.compile(r"^[0-9a-fA-F]{64}$")
_MANIFEST_FIELDS = frozenset(
    {
        "schema_version",
        "app_id",
        "channel",
        "version",
        "installer_asset",
        "installer_size",
        "installer_sha256",
        "notes",
    }
)


class AppUpdateError(RuntimeError):
    """Base class for errors that can be presented by the update UI."""


class UpdateConfigurationError(AppUpdateError):
    """The installed application does not have usable update metadata."""


class UpdateNetworkError(AppUpdateError):
    """An update service request could not be completed."""


class UpdateManifestError(AppUpdateError, ValueError):
    """A release manifest does not satisfy the update contract."""


class UpdateVerificationError(AppUpdateError):
    """A downloaded installer did not match its signed-in manifest metadata."""


class UpdateDownloadCancelled(AppUpdateError):
    """The caller cancelled an installer download."""


# Alternate spelling for callers that use American English.
UpdateDownloadCanceled = UpdateDownloadCancelled


@total_ordering
@dataclass(frozen=True, slots=True)
class SemanticVersion:
    major: int
    minor: int
    patch: int
    prerelease: tuple[int | str, ...] = ()
    build: tuple[str, ...] = ()

    @classmethod
    def parse(cls, value: str) -> SemanticVersion:
        if not isinstance(value, str):
            raise ValueError("Semantic version must be a string.")
        if value != value.strip():
            raise ValueError("Semantic version must not contain surrounding whitespace.")
        match = _SEMANTIC_VERSION_PATTERN.fullmatch(value)
        if match is None:
            raise ValueError(f"Invalid semantic version: {value!r}.")
        raw_prerelease = tuple(
            identifier
            for identifier in (match.group("prerelease") or "").split(".")
            if identifier
        )
        if any(
            identifier.isdigit()
            and len(identifier) > 1
            and identifier.startswith("0")
            for identifier in raw_prerelease
        ):
            raise ValueError(
                f"Invalid semantic version numeric prerelease identifier: {value!r}."
            )
        prerelease: tuple[int | str, ...] = tuple(
            int(identifier) if identifier.isdigit() else identifier
            for identifier in raw_prerelease
        )
        build = tuple(
            identifier
            for identifier in (match.group("build") or "").split(".")
            if identifier
        )
        return cls(
            major=int(match.group("major")),
            minor=int(match.group("minor")),
            patch=int(match.group("patch")),
            prerelease=prerelease,
            build=build,
        )

    def __str__(self) -> str:
        value = f"{self.major}.{self.minor}.{self.patch}"
        if self.prerelease:
            value += "-" + ".".join(str(identifier) for identifier in self.prerelease)
        if self.build:
            value += "+" + ".".join(self.build)
        return value

    def __eq__(self, other: object) -> bool:
        if not isinstance(other, SemanticVersion):
            return NotImplemented
        return (
            self.major,
            self.minor,
            self.patch,
            self.prerelease,
        ) == (
            other.major,
            other.minor,
            other.patch,
            other.prerelease,
        )

    def __hash__(self) -> int:
        return hash((self.major, self.minor, self.patch, self.prerelease))

    def __lt__(self, other: object) -> bool:
        if not isinstance(other, SemanticVersion):
            return NotImplemented
        own_core = (self.major, self.minor, self.patch)
        other_core = (other.major, other.minor, other.patch)
        if own_core != other_core:
            return own_core < other_core
        return _prerelease_is_less(self.prerelease, other.prerelease)


def _prerelease_is_less(
    own_identifiers: tuple[int | str, ...],
    other_identifiers: tuple[int | str, ...],
) -> bool:
    if not own_identifiers:
        return False
    if not other_identifiers:
        return True
    for own_identifier, other_identifier in zip(own_identifiers, other_identifiers):
        if own_identifier == other_identifier:
            continue
        if isinstance(own_identifier, int) and isinstance(other_identifier, int):
            return own_identifier < other_identifier
        if isinstance(own_identifier, int):
            return True
        if isinstance(other_identifier, int):
            return False
        return own_identifier < other_identifier
    return len(own_identifiers) < len(other_identifiers)


def parse_semantic_version(value: str) -> SemanticVersion:
    return SemanticVersion.parse(value)


def compare_semantic_versions(left: str, right: str) -> int:
    left_version = SemanticVersion.parse(left)
    right_version = SemanticVersion.parse(right)
    return (left_version > right_version) - (left_version < right_version)


@dataclass(frozen=True, slots=True)
class UpdateManifest:
    schema_version: int
    app_id: str
    channel: str
    version: str
    installer_asset: str
    installer_size: int
    installer_sha256: str
    notes: str

    @property
    def semantic_version(self) -> SemanticVersion:
        return SemanticVersion.parse(self.version)


@dataclass(frozen=True, slots=True)
class AvailableUpdate:
    manifest: UpdateManifest
    release_tag: str
    release_name: str
    release_url: str
    manifest_url: str
    installer_url: str

    @property
    def version(self) -> str:
        return self.manifest.version

    @property
    def channel(self) -> str:
        return self.manifest.channel

    @property
    def notes(self) -> str:
        return self.manifest.notes

    @property
    def installer_asset(self) -> str:
        return self.manifest.installer_asset

    @property
    def installer_size(self) -> int:
        return self.manifest.installer_size

    @property
    def installer_sha256(self) -> str:
        return self.manifest.installer_sha256


UpdateInfo = AvailableUpdate


@dataclass(frozen=True, slots=True)
class UpdateCheckResult:
    current_version: str
    channel: str
    repository: str
    available_update: AvailableUpdate | None

    @property
    def update_available(self) -> bool:
        return self.available_update is not None

    @property
    def update(self) -> AvailableUpdate | None:
        return self.available_update

    @property
    def latest_version(self) -> str | None:
        if self.available_update is None:
            return None
        return self.available_update.version


@dataclass(frozen=True, slots=True)
class UpdateDownloadResult:
    update: AvailableUpdate
    installer_path: Path
    reused_cached_installer: bool

    @property
    def path(self) -> Path:
        return self.installer_path

    @property
    def from_cache(self) -> bool:
        return self.reused_cached_installer


DownloadResult = UpdateDownloadResult
ProgressCallback = Callable[[int, int], None]
CancellationCallback = Callable[[], bool]


def parse_update_manifest(
    payload: Mapping[str, Any],
    *,
    expected_app_id: str = APP_USER_MODEL_ID,
) -> UpdateManifest:
    if not isinstance(payload, Mapping):
        raise UpdateManifestError("Update manifest must be a JSON object.")
    actual_fields = frozenset(payload.keys())
    if actual_fields != _MANIFEST_FIELDS:
        missing = sorted(_MANIFEST_FIELDS - actual_fields)
        unexpected = sorted(
            (str(key) for key in actual_fields - _MANIFEST_FIELDS),
            key=str.casefold,
        )
        details: list[str] = []
        if missing:
            details.append("missing " + ", ".join(missing))
        if unexpected:
            details.append("unexpected " + ", ".join(unexpected))
        raise UpdateManifestError(
            "Update manifest fields are invalid: " + "; ".join(details) + "."
        )

    schema_version = payload["schema_version"]
    if type(schema_version) is not int or schema_version != UPDATE_MANIFEST_SCHEMA_VERSION:
        raise UpdateManifestError(
            f"Update manifest schema_version must be {UPDATE_MANIFEST_SCHEMA_VERSION}."
        )
    app_id = _strict_manifest_string(payload, "app_id", allow_empty=False)
    if app_id != expected_app_id:
        raise UpdateManifestError(
            f"Update manifest app_id {app_id!r} does not match {expected_app_id!r}."
        )
    channel = _strict_manifest_string(payload, "channel", allow_empty=False)
    if _CHANNEL_PATTERN.fullmatch(channel) is None:
        raise UpdateManifestError("Update manifest channel is invalid.")
    version = _strict_manifest_string(payload, "version", allow_empty=False)
    try:
        semantic_version = SemanticVersion.parse(version)
    except ValueError as exc:
        raise UpdateManifestError(f"Update manifest version is invalid: {exc}") from exc
    _validate_version_channel(semantic_version, channel)

    installer_asset = _strict_manifest_string(
        payload, "installer_asset", allow_empty=False
    )
    if (
        Path(installer_asset).name != installer_asset
        or "/" in installer_asset
        or "\\" in installer_asset
        or installer_asset in {".", ".."}
    ):
        raise UpdateManifestError(
            "Update manifest installer_asset must be a plain file name."
        )
    installer_size = payload["installer_size"]
    if type(installer_size) is not int or installer_size <= 0:
        raise UpdateManifestError(
            "Update manifest installer_size must be a positive integer."
        )
    installer_sha256 = _strict_manifest_string(
        payload, "installer_sha256", allow_empty=False
    )
    if _SHA256_PATTERN.fullmatch(installer_sha256) is None:
        raise UpdateManifestError(
            "Update manifest installer_sha256 must contain 64 hexadecimal characters."
        )
    notes = _strict_manifest_string(payload, "notes", allow_empty=True)
    return UpdateManifest(
        schema_version=schema_version,
        app_id=app_id,
        channel=channel,
        version=version,
        installer_asset=installer_asset,
        installer_size=installer_size,
        installer_sha256=installer_sha256.lower(),
        notes=notes,
    )


def _strict_manifest_string(
    payload: Mapping[str, Any],
    field_name: str,
    *,
    allow_empty: bool,
) -> str:
    value = payload[field_name]
    if not isinstance(value, str):
        raise UpdateManifestError(
            f"Update manifest {field_name} must be a string."
        )
    if value != value.strip():
        raise UpdateManifestError(
            f"Update manifest {field_name} must not contain surrounding whitespace."
        )
    if not allow_empty and not value:
        raise UpdateManifestError(
            f"Update manifest {field_name} must not be empty."
        )
    return value


def _validate_version_channel(version: SemanticVersion, channel: str) -> None:
    normalized_channel = channel.casefold()
    if normalized_channel == "stable":
        if version.prerelease:
            raise UpdateManifestError(
                "A stable-channel update cannot use a prerelease version."
            )
        return
    if not version.prerelease:
        raise UpdateManifestError(
            f"A {channel!r}-channel update must use a prerelease version."
        )
    first_identifier = version.prerelease[0]
    if not isinstance(first_identifier, str) or first_identifier.casefold() != normalized_channel:
        raise UpdateManifestError(
            f"Update version prerelease identifier must start with {channel!r}."
        )


def check_for_updates(
    *,
    repository: str | None = None,
    current_version: str | None = None,
    channel: str | None = None,
    manifest_asset_name: str | None = None,
    http_client: Any = None,
) -> UpdateCheckResult:
    resolved_repository = _validated_repository(
        APP_UPDATE_GITHUB_REPOSITORY if repository is None else repository
    )
    resolved_current_version = APP_VERSION if current_version is None else current_version
    resolved_channel = APP_UPDATE_CHANNEL if channel is None else channel
    resolved_manifest_name = (
        APP_UPDATE_MANIFEST_ASSET_NAME
        if manifest_asset_name is None
        else manifest_asset_name
    )
    if not isinstance(resolved_channel, str) or not resolved_channel:
        raise UpdateConfigurationError("The application update channel is not configured.")
    if not isinstance(resolved_manifest_name, str) or not resolved_manifest_name:
        raise UpdateConfigurationError(
            "The application update manifest asset name is not configured."
        )
    try:
        installed_version = SemanticVersion.parse(resolved_current_version)
    except ValueError as exc:
        raise UpdateConfigurationError(
            f"The installed application version is invalid: {exc}"
        ) from exc

    releases = _fetch_github_releases(
        resolved_repository,
        http_client=http_client,
    )
    best_update: AvailableUpdate | None = None
    first_manifest_error: UpdateManifestError | None = None
    for release in releases:
        if not isinstance(release, Mapping) or release.get("draft") is True:
            continue
        is_prerelease = release.get("prerelease") is True
        if resolved_channel.casefold() == "stable":
            if is_prerelease:
                continue
        elif not is_prerelease:
            continue
        try:
            candidate = _available_update_from_release(
                release,
                channel=resolved_channel,
                manifest_asset_name=resolved_manifest_name,
                http_client=http_client,
            )
        except UpdateManifestError as exc:
            if first_manifest_error is None:
                first_manifest_error = exc
            continue
        if candidate is None or candidate.manifest.semantic_version <= installed_version:
            continue
        if (
            best_update is None
            or candidate.manifest.semantic_version
            > best_update.manifest.semantic_version
        ):
            best_update = candidate
    if best_update is None and first_manifest_error is not None:
        raise first_manifest_error
    return UpdateCheckResult(
        current_version=resolved_current_version,
        channel=resolved_channel,
        repository=resolved_repository,
        available_update=best_update,
    )


check_for_app_update = check_for_updates


def _validated_repository(repository: str) -> str:
    if not isinstance(repository, str) or not repository.strip():
        raise UpdateConfigurationError(
            "Application updates are not configured: set the public GitHub "
            "repository as 'owner/repository'."
        )
    if repository != repository.strip() or _REPOSITORY_PATTERN.fullmatch(repository) is None:
        raise UpdateConfigurationError(
            "The update GitHub repository must use the form 'owner/repository'."
        )
    return repository


def _fetch_github_releases(
    repository: str,
    *,
    http_client: Any,
) -> list[Mapping[str, Any]]:
    releases: list[Mapping[str, Any]] = []
    for page_number in range(1, 11):
        payload = _request_json(
            f"{GITHUB_API_ROOT}/repos/{repository}/releases",
            http_client=http_client,
            params={"per_page": 100, "page": page_number},
            request_label="GitHub Releases API",
        )
        if not isinstance(payload, list):
            raise UpdateNetworkError(
                "GitHub Releases API returned an invalid response; expected a release list."
            )
        releases.extend(item for item in payload if isinstance(item, Mapping))
        if len(payload) < 100:
            return releases
    return releases


def _available_update_from_release(
    release: Mapping[str, Any],
    *,
    channel: str,
    manifest_asset_name: str,
    http_client: Any,
) -> AvailableUpdate | None:
    assets = release.get("assets")
    if not isinstance(assets, list):
        return None
    assets_by_name: dict[str, Mapping[str, Any]] = {}
    duplicate_names: set[str] = set()
    for asset in assets:
        if not isinstance(asset, Mapping):
            continue
        asset_name = asset.get("name")
        if not isinstance(asset_name, str) or not asset_name:
            continue
        if asset_name in assets_by_name:
            duplicate_names.add(asset_name)
        assets_by_name[asset_name] = asset
    if manifest_asset_name in duplicate_names:
        raise UpdateManifestError(
            f"Release contains duplicate {manifest_asset_name!r} manifest assets."
        )
    manifest_asset = assets_by_name.get(manifest_asset_name)
    if manifest_asset is None:
        return None
    manifest_url = _asset_download_url(manifest_asset, manifest_asset_name)
    manifest_payload = _request_json(
        manifest_url,
        http_client=http_client,
        request_label="update manifest",
    )
    manifest = parse_update_manifest(manifest_payload)
    if manifest.channel.casefold() != channel.casefold():
        return None

    if manifest.installer_asset in duplicate_names:
        raise UpdateManifestError(
            f"Release contains duplicate {manifest.installer_asset!r} installer assets."
        )
    installer_asset = assets_by_name.get(manifest.installer_asset)
    if installer_asset is None:
        raise UpdateManifestError(
            f"Release is missing manifest installer asset {manifest.installer_asset!r}."
        )
    installer_url = _asset_download_url(
        installer_asset,
        manifest.installer_asset,
    )
    github_size = installer_asset.get("size")
    if (
        type(github_size) is int
        and github_size >= 0
        and github_size != manifest.installer_size
    ):
        raise UpdateManifestError(
            f"GitHub installer asset size {github_size} does not match manifest "
            f"size {manifest.installer_size}."
        )

    tag_name = release.get("tag_name")
    if isinstance(tag_name, str) and tag_name:
        try:
            tag_version = SemanticVersion.parse(tag_name)
        except ValueError:
            tag_version = None
        if tag_version is not None and tag_version != manifest.semantic_version:
            raise UpdateManifestError(
                f"Release tag {tag_name!r} does not match manifest version "
                f"{manifest.version!r}."
            )
    else:
        tag_name = manifest.version
    release_name = release.get("name")
    if not isinstance(release_name, str):
        release_name = ""
    release_url = release.get("html_url")
    if not isinstance(release_url, str):
        release_url = ""
    return AvailableUpdate(
        manifest=manifest,
        release_tag=tag_name,
        release_name=release_name,
        release_url=release_url,
        manifest_url=manifest_url,
        installer_url=installer_url,
    )


def _asset_download_url(asset: Mapping[str, Any], asset_name: str) -> str:
    download_url = asset.get("browser_download_url")
    if not isinstance(download_url, str) or not download_url.startswith("https://"):
        raise UpdateManifestError(
            f"Release asset {asset_name!r} does not have a secure download URL."
        )
    return download_url


def _request_json(
    url: str,
    *,
    http_client: Any,
    request_label: str,
    params: Mapping[str, Any] | None = None,
) -> Any:
    response = None
    try:
        response = _http_get(
            http_client,
            url,
            headers=_request_headers(),
            params=params,
            timeout=REQUEST_TIMEOUT_SECONDS,
        )
        _ensure_successful_response(response, request_label)
        try:
            return response.json()
        except (json.JSONDecodeError, ValueError, TypeError) as exc:
            raise UpdateNetworkError(
                f"{request_label.capitalize()} returned invalid JSON."
            ) from exc
    except AppUpdateError:
        raise
    except requests.Timeout as exc:
        raise UpdateNetworkError(
            f"{request_label.capitalize()} timed out. Check the internet connection and try again."
        ) from exc
    except requests.RequestException as exc:
        raise UpdateNetworkError(
            f"{request_label.capitalize()} request failed: {_exception_summary(exc)}."
        ) from exc
    finally:
        _close_response(response)


def _http_get(http_client: Any, url: str, **kwargs: Any) -> Any:
    getter = requests.get if http_client is None else http_client.get
    if kwargs.get("params") is None:
        kwargs.pop("params", None)
    return getter(url, **kwargs)


def _ensure_successful_response(response: Any, request_label: str) -> None:
    try:
        response.raise_for_status()
    except requests.HTTPError as exc:
        status_code = getattr(response, "status_code", None)
        headers = getattr(response, "headers", {}) or {}
        message = _response_error_message(response)
        remaining = str(headers.get("X-RateLimit-Remaining", "")).strip()
        if status_code == 429 or (
            status_code == 403
            and (remaining == "0" or "rate limit" in message.casefold())
        ):
            reset_summary = _rate_limit_reset_summary(headers)
            raise UpdateNetworkError(
                "GitHub API rate limit exceeded"
                + (f"; retry {reset_summary}" if reset_summary else "")
                + "."
            ) from exc
        status_summary = (
            f"HTTP {status_code}" if isinstance(status_code, int) else "an HTTP error"
        )
        raise UpdateNetworkError(
            f"{request_label.capitalize()} failed with {status_summary}"
            + (f": {message}" if message else "")
            + "."
        ) from exc


def _response_error_message(response: Any) -> str:
    try:
        payload = response.json()
    except (ValueError, TypeError, AttributeError):
        payload = None
    if isinstance(payload, Mapping) and isinstance(payload.get("message"), str):
        message = payload["message"].strip()
    else:
        text = getattr(response, "text", "")
        message = text.strip() if isinstance(text, str) else ""
    return " ".join(message.split())[:240]


def _rate_limit_reset_summary(headers: Mapping[str, Any]) -> str:
    retry_after = str(headers.get("Retry-After", "")).strip()
    if retry_after:
        return f"after {retry_after} seconds"
    reset_value = str(headers.get("X-RateLimit-Reset", "")).strip()
    try:
        reset_at = datetime.fromtimestamp(int(reset_value), tz=UTC)
    except (ValueError, TypeError, OverflowError, OSError):
        return ""
    return f"after {reset_at.strftime('%Y-%m-%d %H:%M UTC')}"


def _exception_summary(exc: BaseException) -> str:
    message = " ".join(str(exc).split())
    return message[:240] or type(exc).__name__


def _request_headers() -> dict[str, str]:
    return {
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
        "User-Agent": f"CitizenAstronomy/{APP_VERSION}",
    }


def default_updates_directory() -> Path:
    local_app_data = os.environ.get("LOCALAPPDATA")
    if local_app_data:
        root = Path(local_app_data)
    elif os.name == "nt":
        root = Path.home() / "AppData" / "Local"
    else:
        root = Path.home() / ".local" / "share"
    return root / "CitizenAstronomy" / "updates"


def download_update(
    update: AvailableUpdate,
    *,
    updates_directory: Path | str | None = None,
    progress_callback: ProgressCallback | None = None,
    cancellation_callback: CancellationCallback | None = None,
    http_client: Any = None,
) -> UpdateDownloadResult:
    if not isinstance(update, AvailableUpdate):
        raise TypeError("update must be an AvailableUpdate.")
    destination_directory = (
        default_updates_directory()
        if updates_directory is None
        else Path(updates_directory).expanduser()
    )
    destination_directory.mkdir(parents=True, exist_ok=True)
    destination = destination_directory / update.installer_asset
    partial_destination = destination.with_name(destination.name + ".part")

    _remove_incomplete_downloads(destination_directory)
    if _file_matches_manifest(destination, update.manifest):
        _remove_obsolete_installers(destination_directory, preserve=destination)
        _emit_progress(progress_callback, update.installer_size, update.installer_size)
        return UpdateDownloadResult(update, destination, True)
    _unlink_file(destination)
    _remove_obsolete_installers(destination_directory, preserve=destination)
    if _is_cancelled(cancellation_callback):
        raise UpdateDownloadCancelled("Update download was cancelled.")

    response = None
    downloaded_size = 0
    digest = hashlib.sha256()
    try:
        response = _http_get(
            http_client,
            update.installer_url,
            headers=_request_headers(),
            stream=True,
            timeout=DOWNLOAD_TIMEOUT_SECONDS,
        )
        _ensure_successful_response(response, "installer download")
        _validate_content_length(response, update.installer_size)
        _emit_progress(progress_callback, 0, update.installer_size)
        with partial_destination.open("wb") as output:
            for chunk in response.iter_content(chunk_size=DOWNLOAD_CHUNK_SIZE):
                if _is_cancelled(cancellation_callback):
                    raise UpdateDownloadCancelled("Update download was cancelled.")
                if not chunk:
                    continue
                output.write(chunk)
                digest.update(chunk)
                downloaded_size += len(chunk)
                if downloaded_size > update.installer_size:
                    raise UpdateVerificationError(
                        "Downloaded installer is larger than the manifest size."
                    )
                _emit_progress(
                    progress_callback,
                    downloaded_size,
                    update.installer_size,
                )
            output.flush()
            os.fsync(output.fileno())
        _verify_download(
            downloaded_size=downloaded_size,
            actual_sha256=digest.hexdigest(),
            manifest=update.manifest,
        )
        os.replace(partial_destination, destination)
        _remove_obsolete_installers(destination_directory, preserve=destination)
        return UpdateDownloadResult(update, destination, False)
    except AppUpdateError:
        _unlink_file(partial_destination)
        raise
    except requests.Timeout as exc:
        _unlink_file(partial_destination)
        raise UpdateNetworkError(
            "Installer download timed out; the incomplete download was removed."
        ) from exc
    except requests.RequestException as exc:
        _unlink_file(partial_destination)
        raise UpdateNetworkError(
            "Installer download was interrupted: "
            f"{_exception_summary(exc)}; the incomplete download was removed."
        ) from exc
    except OSError as exc:
        _unlink_file(partial_destination)
        raise AppUpdateError(
            f"Could not store the update installer: {_exception_summary(exc)}."
        ) from exc
    finally:
        _close_response(response)


def download_update_installer(
    update: AvailableUpdate,
    *,
    updates_directory: Path | str | None = None,
    progress_callback: ProgressCallback | None = None,
    cancellation_requested: CancellationCallback | None = None,
    cancellation_callback: CancellationCallback | None = None,
    http_client: Any = None,
) -> Path:
    """Download an installer and return the path expected by UI workers."""
    if cancellation_requested is not None and cancellation_callback is not None:
        raise TypeError(
            "Pass either cancellation_requested or cancellation_callback, not both."
        )
    result = download_update(
        update,
        updates_directory=updates_directory,
        progress_callback=progress_callback,
        cancellation_callback=(
            cancellation_callback
            if cancellation_callback is not None
            else cancellation_requested
        ),
        http_client=http_client,
    )
    return result.installer_path


download_installer = download_update_installer


def _validate_content_length(response: Any, expected_size: int) -> None:
    headers = getattr(response, "headers", {}) or {}
    raw_content_length = headers.get("Content-Length")
    if raw_content_length in {None, ""}:
        return
    try:
        content_length = int(raw_content_length)
    except (TypeError, ValueError):
        return
    if content_length != expected_size:
        raise UpdateVerificationError(
            f"Installer Content-Length {content_length} does not match manifest "
            f"size {expected_size}."
        )


def _verify_download(
    *,
    downloaded_size: int,
    actual_sha256: str,
    manifest: UpdateManifest,
) -> None:
    if downloaded_size != manifest.installer_size:
        raise UpdateVerificationError(
            f"Downloaded installer size {downloaded_size} does not match manifest "
            f"size {manifest.installer_size}."
        )
    if actual_sha256.casefold() != manifest.installer_sha256.casefold():
        raise UpdateVerificationError(
            "Downloaded installer SHA-256 does not match the update manifest."
        )


def _file_matches_manifest(path: Path, manifest: UpdateManifest) -> bool:
    try:
        if not path.is_file() or path.stat().st_size != manifest.installer_size:
            return False
        digest = hashlib.sha256()
        with path.open("rb") as source:
            for chunk in iter(lambda: source.read(DOWNLOAD_CHUNK_SIZE), b""):
                digest.update(chunk)
        return digest.hexdigest().casefold() == manifest.installer_sha256.casefold()
    except OSError:
        return False


def _remove_incomplete_downloads(directory: Path) -> None:
    try:
        candidates = tuple(directory.iterdir())
    except OSError:
        return
    for candidate in candidates:
        if candidate.is_file() and candidate.name.endswith(".part"):
            _unlink_file(candidate)


def _remove_obsolete_installers(directory: Path, *, preserve: Path) -> None:
    try:
        candidates = tuple(directory.iterdir())
    except OSError:
        return
    installer_suffixes = {".exe", ".msi", ".msix", ".part"}
    for candidate in candidates:
        if candidate == preserve or not candidate.is_file():
            continue
        if candidate.suffix.casefold() in installer_suffixes:
            _unlink_file(candidate)


def _unlink_file(path: Path) -> None:
    try:
        path.unlink(missing_ok=True)
    except OSError:
        pass


def _is_cancelled(callback: CancellationCallback | None) -> bool:
    return bool(callback is not None and callback())


def _emit_progress(
    callback: ProgressCallback | None,
    downloaded: int,
    total: int,
) -> None:
    if callback is not None:
        callback(downloaded, total)


def _close_response(response: Any) -> None:
    if response is None:
        return
    close = getattr(response, "close", None)
    if callable(close):
        close()


__all__ = [
    "AppUpdateError",
    "AvailableUpdate",
    "CancellationCallback",
    "DownloadResult",
    "ProgressCallback",
    "SemanticVersion",
    "UpdateCheckResult",
    "UpdateConfigurationError",
    "UpdateDownloadCanceled",
    "UpdateDownloadCancelled",
    "UpdateDownloadResult",
    "UpdateInfo",
    "UpdateManifest",
    "UpdateManifestError",
    "UpdateNetworkError",
    "UpdateVerificationError",
    "check_for_app_update",
    "check_for_updates",
    "compare_semantic_versions",
    "default_updates_directory",
    "download_installer",
    "download_update",
    "download_update_installer",
    "parse_semantic_version",
    "parse_update_manifest",
]
