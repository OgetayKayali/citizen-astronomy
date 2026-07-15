from __future__ import annotations

from dataclasses import FrozenInstanceError
import hashlib
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import requests

from photometry_app.core import app_updates
from photometry_app.core.app_updates import (
    AppUpdateError,
    AvailableUpdate,
    SemanticVersion,
    UpdateConfigurationError,
    UpdateDownloadCancelled,
    UpdateManifestError,
    UpdateNetworkError,
    UpdateVerificationError,
    check_for_updates,
    download_update,
    parse_update_manifest,
)


_MANIFEST_ASSET_NAME = "CitizenAstronomy-update.json"
_INSTALLER_NAME = "CitizenAstronomyAlphaReview-0.1.2-alpha.1-Setup.exe"


class _FakeResponse:
    def __init__(
        self,
        *,
        payload: object | None = None,
        chunks: tuple[bytes, ...] = (),
        status_code: int = 200,
        headers: dict[str, str] | None = None,
        text: str = "",
        iteration_error: BaseException | None = None,
    ) -> None:
        self._payload = payload
        self._chunks = chunks
        self.status_code = status_code
        self.headers = headers or {}
        self.text = text
        self.iteration_error = iteration_error
        self.closed = False

    def raise_for_status(self) -> None:
        if self.status_code >= 400:
            raise requests.HTTPError(
                f"HTTP {self.status_code}",
                response=self,  # type: ignore[arg-type]
            )

    def json(self) -> object:
        if isinstance(self._payload, BaseException):
            raise self._payload
        return self._payload

    def iter_content(self, chunk_size: int) -> object:
        del chunk_size
        for chunk in self._chunks:
            yield chunk
        if self.iteration_error is not None:
            raise self.iteration_error

    def close(self) -> None:
        self.closed = True


class _FakeHttpClient:
    def __init__(self, responses: dict[str, list[_FakeResponse]]) -> None:
        self.responses = {url: list(items) for url, items in responses.items()}
        self.calls: list[tuple[str, dict[str, object]]] = []

    def get(self, url: str, **kwargs: object) -> _FakeResponse:
        self.calls.append((url, kwargs))
        try:
            return self.responses[url].pop(0)
        except (KeyError, IndexError) as exc:
            raise AssertionError(f"Unexpected HTTP request: {url}") from exc


def _manifest_payload(
    installer: bytes,
    *,
    version: str = "0.1.2-alpha.1",
    channel: str = "alpha",
    installer_name: str = _INSTALLER_NAME,
) -> dict[str, object]:
    return {
        "schema_version": 1,
        "app_id": "CitizenAstronomy.CAst",
        "channel": channel,
        "version": version,
        "installer_asset": installer_name,
        "installer_size": len(installer),
        "installer_sha256": hashlib.sha256(installer).hexdigest(),
        "notes": f"Changes in {version}",
    }


def _release(
    manifest_url: str,
    installer_url: str,
    manifest: dict[str, object],
    *,
    draft: bool = False,
    prerelease: bool = True,
) -> dict[str, object]:
    version = str(manifest["version"])
    installer_name = str(manifest["installer_asset"])
    return {
        "draft": draft,
        "prerelease": prerelease,
        "tag_name": f"v{version}",
        "name": version,
        "html_url": f"https://github.test/releases/{version}",
        "assets": [
            {
                "name": _MANIFEST_ASSET_NAME,
                "browser_download_url": manifest_url,
            },
            {
                "name": installer_name,
                "browser_download_url": installer_url,
                "size": manifest["installer_size"],
            },
        ],
    }


def _available_update(installer: bytes) -> AvailableUpdate:
    manifest = parse_update_manifest(_manifest_payload(installer))
    return AvailableUpdate(
        manifest=manifest,
        release_tag=f"v{manifest.version}",
        release_name=manifest.version,
        release_url=f"https://github.test/releases/{manifest.version}",
        manifest_url="https://downloads.test/update.json",
        installer_url="https://downloads.test/installer.exe",
    )


class SemanticVersionTest(unittest.TestCase):
    def test_semantic_version_ordering_handles_numeric_prereleases_and_stable(self) -> None:
        ordered = [
            "0.1.1-alpha",
            "0.1.1-alpha.1",
            "0.1.1-alpha.2",
            "0.1.1-alpha.10",
            "0.1.1-beta.1",
            "0.1.1-rc.1",
            "0.1.1",
            "0.1.2-alpha.1",
        ]
        parsed = [SemanticVersion.parse(value) for value in reversed(ordered)]

        self.assertEqual([str(value) for value in sorted(parsed)], ordered)
        self.assertEqual(
            SemanticVersion.parse("v1.2.3+build.7"),
            SemanticVersion.parse("1.2.3+other"),
        )
        self.assertLess(
            SemanticVersion.parse("1.2.3-1alpha"),
            SemanticVersion.parse("1.2.3-alpha"),
        )
        for malformed in ("1.2", "01.2.3", "1.2.3-alpha.01", "1.2.3-"):
            with self.subTest(malformed=malformed):
                with self.assertRaises(ValueError):
                    SemanticVersion.parse(malformed)


class UpdateManifestTest(unittest.TestCase):
    def test_manifest_schema_is_strict_and_result_is_immutable(self) -> None:
        installer = b"installer"
        payload = _manifest_payload(installer)
        manifest = parse_update_manifest(payload)

        self.assertEqual(manifest.semantic_version, SemanticVersion.parse("0.1.2-alpha.1"))
        with self.assertRaises(FrozenInstanceError):
            manifest.version = "9.9.9"  # type: ignore[misc]

        invalid_payloads: list[dict[str, object]] = []
        missing_notes = dict(payload)
        missing_notes.pop("notes")
        invalid_payloads.append(missing_notes)
        extra_field = dict(payload, extra="not allowed")
        invalid_payloads.append(extra_field)
        invalid_payloads.append(dict(payload, installer_size=True))
        invalid_payloads.append(dict(payload, installer_sha256="abc"))
        invalid_payloads.append(dict(payload, installer_asset="../setup.exe"))
        invalid_payloads.append(dict(payload, version="0.1.2", channel="alpha"))
        invalid_payloads.append(dict(payload, app_id="another.application"))

        for invalid_payload in invalid_payloads:
            with self.subTest(payload=invalid_payload):
                with self.assertRaises(UpdateManifestError):
                    parse_update_manifest(invalid_payload)


class UpdateDiscoveryTest(unittest.TestCase):
    def test_prerelease_discovery_skips_drafts_and_selects_highest_matching_channel(
        self,
    ) -> None:
        installer = b"release installer"
        manifests = {
            "stable": _manifest_payload(
                installer,
                version="9.0.0",
                channel="stable",
                installer_name="stable.exe",
            ),
            "beta": _manifest_payload(
                installer,
                version="2.0.0-beta.1",
                channel="beta",
                installer_name="beta.exe",
            ),
            "alpha2": _manifest_payload(
                installer,
                version="0.1.2-alpha.2",
                installer_name="alpha2.exe",
            ),
            "alpha10": _manifest_payload(
                installer,
                version="0.1.2-alpha.10",
                installer_name="alpha10.exe",
            ),
            "draft": _manifest_payload(
                installer,
                version="99.0.0-alpha.1",
                installer_name="draft.exe",
            ),
            "nonprerelease_alpha": _manifest_payload(
                installer,
                version="98.0.0-alpha.1",
                installer_name="nonprerelease-alpha.exe",
            ),
        }
        releases = [
            _release(
                "https://downloads.test/draft.json",
                "https://downloads.test/draft.exe",
                manifests["draft"],
                draft=True,
            ),
            _release(
                "https://downloads.test/nonprerelease-alpha.json",
                "https://downloads.test/nonprerelease-alpha.exe",
                manifests["nonprerelease_alpha"],
                prerelease=False,
            ),
            _release(
                "https://downloads.test/stable.json",
                "https://downloads.test/stable.exe",
                manifests["stable"],
                prerelease=False,
            ),
            _release(
                "https://downloads.test/beta.json",
                "https://downloads.test/beta.exe",
                manifests["beta"],
            ),
            _release(
                "https://downloads.test/alpha2.json",
                "https://downloads.test/alpha2.exe",
                manifests["alpha2"],
            ),
            _release(
                "https://downloads.test/alpha10.json",
                "https://downloads.test/alpha10.exe",
                manifests["alpha10"],
            ),
        ]
        releases_url = "https://api.github.com/repos/owner/releases/releases"
        client = _FakeHttpClient(
            {
                releases_url: [_FakeResponse(payload=releases)],
                "https://downloads.test/stable.json": [
                    _FakeResponse(payload=manifests["stable"])
                ],
                "https://downloads.test/beta.json": [
                    _FakeResponse(payload=manifests["beta"])
                ],
                "https://downloads.test/alpha2.json": [
                    _FakeResponse(payload=manifests["alpha2"])
                ],
                "https://downloads.test/alpha10.json": [
                    _FakeResponse(payload=manifests["alpha10"])
                ],
            }
        )

        result = check_for_updates(
            repository="owner/releases",
            current_version="0.1.1-alpha.1",
            channel="alpha",
            http_client=client,
        )

        self.assertTrue(result.update_available)
        self.assertIsNotNone(result.available_update)
        assert result.available_update is not None
        self.assertEqual(result.available_update.version, "0.1.2-alpha.10")
        self.assertEqual(result.latest_version, "0.1.2-alpha.10")
        self.assertFalse(
            any(url == "https://downloads.test/draft.json" for url, _kwargs in client.calls)
        )
        self.assertFalse(
            any(url == "https://downloads.test/nonprerelease-alpha.json" for url, _kwargs in client.calls)
        )
        api_kwargs = client.calls[0][1]
        self.assertEqual(api_kwargs["timeout"], app_updates.REQUEST_TIMEOUT_SECONDS)
        self.assertIn("User-Agent", api_kwargs["headers"])  # type: ignore[operator]
        self.assertNotIn("Authorization", api_kwargs["headers"])  # type: ignore[operator]

    def test_no_newer_matching_release_returns_empty_result(self) -> None:
        releases_url = "https://api.github.com/repos/owner/releases/releases"
        client = _FakeHttpClient(
            {releases_url: [_FakeResponse(payload=[])]}
        )

        result = check_for_updates(
            repository="owner/releases",
            current_version="0.1.2-alpha.1",
            channel="alpha",
            http_client=client,
        )

        self.assertFalse(result.update_available)
        self.assertIsNone(result.update)

    def test_malformed_release_manifest_is_reported(self) -> None:
        installer = b"release installer"
        payload = _manifest_payload(installer)
        payload.pop("notes")
        release = _release(
            "https://downloads.test/bad.json",
            "https://downloads.test/installer.exe",
            payload,
        )
        releases_url = "https://api.github.com/repos/owner/releases/releases"
        client = _FakeHttpClient(
            {
                releases_url: [_FakeResponse(payload=[release])],
                "https://downloads.test/bad.json": [_FakeResponse(payload=payload)],
            }
        )

        with self.assertRaisesRegex(UpdateManifestError, "missing notes"):
            check_for_updates(
                repository="owner/releases",
                current_version="0.1.1-alpha.1",
                channel="alpha",
                http_client=client,
            )

    def test_no_repository_configuration_fails_before_network_access(self) -> None:
        client = _FakeHttpClient({})

        with self.assertRaisesRegex(UpdateConfigurationError, "not configured"):
            check_for_updates(repository="", http_client=client)

        self.assertEqual(client.calls, [])

    def test_network_timeout_has_short_user_facing_summary(self) -> None:
        class _TimeoutClient:
            def get(self, url: str, **kwargs: object) -> _FakeResponse:
                del url, kwargs
                raise requests.Timeout("connect timed out")

        with self.assertRaisesRegex(UpdateNetworkError, "timed out"):
            check_for_updates(
                repository="owner/releases",
                http_client=_TimeoutClient(),
            )

    def test_github_rate_limit_error_reports_retry_time_without_response_dump(
        self,
    ) -> None:
        releases_url = "https://api.github.com/repos/owner/releases/releases"
        response = _FakeResponse(
            payload={"message": "API rate limit exceeded"},
            status_code=403,
            headers={
                "X-RateLimit-Remaining": "0",
                "Retry-After": "60",
            },
        )
        client = _FakeHttpClient({releases_url: [response]})

        with self.assertRaisesRegex(
            UpdateNetworkError,
            r"rate limit exceeded; retry after 60 seconds",
        ):
            check_for_updates(
                repository="owner/releases",
                http_client=client,
            )


class UpdateDownloadTest(unittest.TestCase):
    def test_cancelled_download_removes_partial_file(self) -> None:
        installer = b"first chunk-second chunk"
        update = _available_update(installer)
        response = _FakeResponse(chunks=(b"first chunk-", b"second chunk"))
        client = _FakeHttpClient({update.installer_url: [response]})
        cancellation_checks = iter((False, False, True))

        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            (root / "stale.exe.part").write_bytes(b"stale")
            with self.assertRaises(UpdateDownloadCancelled):
                download_update(
                    update,
                    updates_directory=root,
                    cancellation_callback=lambda: next(cancellation_checks),
                    http_client=client,
                )

            self.assertFalse((root / update.installer_asset).exists())
            self.assertEqual(list(root.glob("*.part")), [])
        self.assertTrue(response.closed)

    def test_interrupted_http_stream_removes_partial_file(self) -> None:
        installer = b"first chunk-second chunk"
        update = _available_update(installer)
        response = _FakeResponse(
            chunks=(b"first chunk-",),
            iteration_error=requests.ConnectionError("connection dropped"),
        )
        client = _FakeHttpClient({update.installer_url: [response]})

        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            with self.assertRaisesRegex(UpdateNetworkError, "interrupted"):
                download_update(
                    update,
                    updates_directory=root,
                    http_client=client,
                )

            self.assertFalse((root / update.installer_asset).exists())
            self.assertEqual(list(root.glob("*.part")), [])

    def test_size_and_hash_mismatches_never_publish_installer(self) -> None:
        expected_installer = b"expected bytes"
        update = _available_update(expected_installer)
        mismatches = (
            (b"short", "size"),
            (b"x" * len(expected_installer), "SHA-256"),
        )

        for downloaded_installer, expected_message in mismatches:
            with self.subTest(expected_message=expected_message):
                response = _FakeResponse(chunks=(downloaded_installer,))
                client = _FakeHttpClient({update.installer_url: [response]})
                with tempfile.TemporaryDirectory() as temporary_directory:
                    root = Path(temporary_directory)
                    with self.assertRaisesRegex(
                        UpdateVerificationError,
                        expected_message,
                    ):
                        download_update(
                            update,
                            updates_directory=root,
                            http_client=client,
                        )

                    self.assertFalse((root / update.installer_asset).exists())
                    self.assertEqual(list(root.glob("*.part")), [])

    def test_success_uses_atomic_replace_then_reuses_valid_cached_installer(
        self,
    ) -> None:
        installer = b"a complete and verified installer"
        update = _available_update(installer)
        response = _FakeResponse(
            chunks=(installer[:10], installer[10:]),
            headers={"Content-Length": str(len(installer))},
        )
        client = _FakeHttpClient({update.installer_url: [response]})
        progress: list[tuple[int, int]] = []

        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            obsolete = root / "old-installer.exe"
            obsolete.write_bytes(b"old")
            stale_partial = root / "abandoned.exe.part"
            stale_partial.write_bytes(b"incomplete")
            real_replace = os.replace
            with patch.object(
                app_updates.os,
                "replace",
                wraps=real_replace,
            ) as atomic_replace:
                result = download_update(
                    update,
                    updates_directory=root,
                    progress_callback=lambda downloaded, total: progress.append(
                        (downloaded, total)
                    ),
                    http_client=client,
                )

            self.assertFalse(result.from_cache)
            self.assertEqual(result.installer_path.read_bytes(), installer)
            atomic_replace.assert_called_once_with(
                root / f"{update.installer_asset}.part",
                root / update.installer_asset,
            )
            self.assertFalse(obsolete.exists())
            self.assertFalse(stale_partial.exists())
            self.assertEqual(progress[0], (0, len(installer)))
            self.assertEqual(progress[-1], (len(installer), len(installer)))

            cached_progress: list[tuple[int, int]] = []
            cached = download_update(
                update,
                updates_directory=root,
                progress_callback=lambda downloaded, total: cached_progress.append(
                    (downloaded, total)
                ),
                http_client=_FakeHttpClient({}),
            )

            self.assertTrue(cached.reused_cached_installer)
            self.assertEqual(cached.installer_path, result.installer_path)
            self.assertEqual(cached_progress, [(len(installer), len(installer))])
            worker_path = app_updates.download_update_installer(
                update,
                updates_directory=root,
                cancellation_requested=lambda: False,
                http_client=_FakeHttpClient({}),
            )
            self.assertEqual(worker_path, result.installer_path)

    def test_local_app_data_default_matches_application_update_directory(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            with patch.dict(
                os.environ,
                {"LOCALAPPDATA": temporary_directory},
                clear=False,
            ):
                expected = (
                    Path(temporary_directory)
                    / "CitizenAstronomy"
                    / "updates"
                )
                self.assertEqual(app_updates.default_updates_directory(), expected)

    def test_filesystem_write_error_has_update_error_summary(self) -> None:
        installer = b"installer"
        update = _available_update(installer)
        response = _FakeResponse(chunks=(installer,))
        client = _FakeHttpClient({update.installer_url: [response]})

        with tempfile.TemporaryDirectory() as temporary_directory:
            with patch.object(Path, "open", side_effect=OSError("disk unavailable")):
                with self.assertRaisesRegex(AppUpdateError, "disk unavailable"):
                    download_update(
                        update,
                        updates_directory=Path(temporary_directory),
                        http_client=client,
                    )


if __name__ == "__main__":
    unittest.main()
