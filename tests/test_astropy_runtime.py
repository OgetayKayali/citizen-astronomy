from __future__ import annotations

import unittest
from pathlib import Path
from unittest.mock import patch

from photometry_app.core import astropy_runtime


class AstropyRuntimeTest(unittest.TestCase):
    def test_fallback_contents_for_crossdomain(self) -> None:
        contents = astropy_runtime._fallback_contents("crossdomain.xml", encoding="utf-8")
        self.assertIsInstance(contents, str)
        self.assertIn("cross-domain-policy", contents)

    def test_fallback_path_writes_icon(self) -> None:
        path = astropy_runtime._fallback_path("astropy_icon.png")
        self.assertTrue(path.is_file())
        self.assertGreater(path.stat().st_size, 0)

    def test_optional_failure_skips_query_criteria_json(self) -> None:
        self.assertFalse(
            astropy_runtime._is_optional_astropy_data_failure(
                "http://data.astropy.org/data/query_criteria_fields.json HTTP Error 404"
            )
        )
        self.assertTrue(
            astropy_runtime._is_optional_astropy_data_failure(
                "http://data.astropy.org/data/astropy_icon.png HTTP Error 404"
            )
        )

    def test_resolve_local_simbad_criteria_json(self) -> None:
        path = astropy_runtime._resolve_local_package_data_file(
            str(Path("data") / "query_criteria_fields.json")
        )
        self.assertIsNotNone(path)
        assert path is not None
        self.assertTrue(Path(path).is_file())
        self.assertGreater(Path(path).stat().st_size, 100)

    def test_install_serves_simbad_criteria_without_download(self) -> None:
        from astropy.utils import data as astropy_data

        # Force a clean install of the current wrapper implementation.
        if hasattr(astropy_data, "_citizen_astronomy_pkgdata_fallback"):
            delattr(astropy_data, "_citizen_astronomy_pkgdata_fallback")

        original_download = astropy_data.download_file

        def fail_download(*_args, **_kwargs):
            raise AssertionError("download_file should not be called for local SIMBAD criteria JSON")

        with patch.object(astropy_data, "download_file", side_effect=fail_download):
            # Reinstall against the already-patched module state by clearing the flag first.
            astropy_runtime.install_astropy_pkgdata_fallback()
            # Replace download again after install so the wrapper's original_download is the failing one.
            wrapped_download = astropy_data.download_file

            def wrapped_fail(remote_url, *args, **kwargs):
                local = astropy_runtime._resolve_local_package_data_file(remote_url)
                if local is not None:
                    return local
                raise AssertionError(f"unexpected download for {remote_url!r}")

            astropy_data.download_file = wrapped_fail  # type: ignore[assignment]
            try:
                path = astropy_data.get_pkg_data_filename(
                    str(Path("data") / "query_criteria_fields.json")
                )
            finally:
                astropy_data.download_file = wrapped_download  # type: ignore[assignment]

        self.assertTrue(Path(path).is_file())
        self.assertIn("query_criteria_fields.json", path.replace("\\", "/"))

        # Restore a normal install for later tests in this process.
        if hasattr(astropy_data, "_citizen_astronomy_pkgdata_fallback"):
            delattr(astropy_data, "_citizen_astronomy_pkgdata_fallback")
        astropy_data.download_file = original_download  # type: ignore[assignment]
        astropy_runtime.install_astropy_pkgdata_fallback()

    def test_install_is_idempotent(self) -> None:
        from astropy.utils import data as astropy_data

        if hasattr(astropy_data, "_citizen_astronomy_pkgdata_fallback"):
            delattr(astropy_data, "_citizen_astronomy_pkgdata_fallback")
        astropy_runtime.install_astropy_pkgdata_fallback()
        first = astropy_data.get_pkg_data_contents
        astropy_runtime.install_astropy_pkgdata_fallback()
        self.assertIs(astropy_data.get_pkg_data_contents, first)


if __name__ == "__main__":
    unittest.main()
