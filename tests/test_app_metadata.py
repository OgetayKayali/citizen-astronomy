from __future__ import annotations

import os
import unittest
from unittest.mock import patch

from photometry_app import app_metadata


class AppMetadataTest(unittest.TestCase):
    def test_managed_updates_supported_matches_windows_only_flag(self) -> None:
        with patch.object(app_metadata, "APP_MANAGED_UPDATES_SUPPORTED", True):
            self.assertTrue(app_metadata.managed_updates_supported())
        with patch.object(app_metadata, "APP_MANAGED_UPDATES_SUPPORTED", False):
            self.assertFalse(app_metadata.managed_updates_supported())

    def test_default_managed_updates_flag_follows_os_name(self) -> None:
        self.assertEqual(app_metadata.APP_MANAGED_UPDATES_SUPPORTED, os.name == "nt")

    def test_icon_candidates_include_icns_for_macos_bundles(self) -> None:
        self.assertEqual(app_metadata._APP_ICON_CANDIDATE_NAMES[0], "citizen_astronomy.icns")


if __name__ == "__main__":
    unittest.main()
