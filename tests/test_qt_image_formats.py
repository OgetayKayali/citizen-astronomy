from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from photometry_app.core.qt_image_formats import (
    QtImageFormatSupport,
    format_qt_image_format_support_for_log,
    qt_image_decode_failure_reason,
    query_qt_image_format_support,
)
from photometry_app.core.qt_image_format_smoke import build_qt_image_format_smoke_result


class QtImageFormatSupportTest(unittest.TestCase):
    def test_qt_image_format_smoke_result_reports_expected_shape(self) -> None:
        result = build_qt_image_format_smoke_result()

        self.assertIn("success", result)
        self.assertIn("decode_success", result)
        self.assertIn("qt_image_support", result)
        self.assertEqual(result["dimensions"], [2, 2])
        self.assertTrue(result["decode_success"])
        self.assertTrue(result["pixel_match"])

    def test_query_qt_image_format_support_reports_flags_and_plugins(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            plugin_root = Path(temp_dir) / "plugins"
            imageformats_dir = plugin_root / "imageformats"
            imageformats_dir.mkdir(parents=True)
            (imageformats_dir / "qtiff.dll").write_bytes(b"")
            (imageformats_dir / "qpng.dll").write_bytes(b"")

            with (
                patch("photometry_app.core.qt_image_formats.QImageReader.supportedImageFormats", return_value=[b"png", b"tif", b"webp"]),
                patch("photometry_app.core.qt_image_formats.QLibraryInfo.path", return_value=str(plugin_root)),
                patch("photometry_app.core.qt_image_formats.QCoreApplication.libraryPaths", return_value=[str(plugin_root)]),
            ):
                support = query_qt_image_format_support()

        self.assertEqual(support.supported_formats, ("png", "tif", "webp"))
        self.assertTrue(support.has_tiff)
        self.assertTrue(support.has_png)
        self.assertTrue(support.has_webp)
        self.assertEqual(support.qt_plugins_path, str(plugin_root))
        self.assertEqual(support.imageformats_plugin_directories, (str(imageformats_dir),))
        self.assertEqual(support.imageformats_plugins, ("qpng.dll", "qtiff.dll"))

    def test_tiff_decode_failure_reason_is_specific_for_tiff_paths(self) -> None:
        support = QtImageFormatSupport(
            supported_formats=("png",),
            has_tiff=False,
            has_png=True,
            has_webp=False,
            qt_plugins_path="plugins",
            library_paths=("plugins",),
            imageformats_plugin_directories=("plugins/imageformats",),
            imageformats_plugins=("qpng.dll",),
        )

        self.assertEqual(
            qt_image_decode_failure_reason("tile.tiff", support=support),
            "TIFF tile decode failed; Qt TIFF image plugin unavailable or decode error.",
        )
        self.assertEqual(
            qt_image_decode_failure_reason("tile.png", support=support),
            "Tile image decode failed; QImage.fromData returned a null image.",
        )

    def test_format_qt_image_format_support_for_log_includes_expected_fields(self) -> None:
        support = QtImageFormatSupport(
            supported_formats=("png", "tif", "webp"),
            has_tiff=True,
            has_png=True,
            has_webp=True,
            qt_plugins_path="plugins",
            library_paths=("plugins",),
            imageformats_plugin_directories=("plugins/imageformats",),
            imageformats_plugins=("qpng.dll", "qtiff.dll"),
        )

        summary = format_qt_image_format_support_for_log(support)

        self.assertIn("supported:png|tif|webp", summary)
        self.assertIn("has_tiff:1", summary)
        self.assertIn("imageformats_plugins:qpng.dll|qtiff.dll", summary)