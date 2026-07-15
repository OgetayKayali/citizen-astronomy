from __future__ import annotations

import json
from pathlib import Path

import numpy as np
from PySide6.QtCore import QCoreApplication
from PySide6.QtGui import QImage, QImageReader

from photometry_app.core.image_io import read_header_and_shape, read_photometry_image_data
from photometry_app.core.qt_image_format_smoke import build_qt_image_format_smoke_result


def _fixture_path(fixtures_dir: Path, name: str) -> Path:
    return (fixtures_dir / name).resolve()


def _check_qt_image_file(path: Path) -> dict[str, object]:
    reader = QImageReader(str(path))
    image = reader.read()
    if image.isNull():
        return {
            "success": False,
            "path": str(path),
            "error": reader.errorString() or "QImageReader returned a null image",
            "dimensions": [0, 0],
        }
    return {
        "success": True,
        "path": str(path),
        "error": "",
        "dimensions": [int(image.width()), int(image.height())],
    }


def _check_fits_file(path: Path) -> dict[str, object]:
    if not path.is_file():
        return {"success": False, "path": str(path), "error": "fixture missing", "dimensions": [0, 0]}
    try:
        _header, width, height = read_header_and_shape(path)
        if width is None or height is None:
            raise ValueError("FITS header did not expose image dimensions")
        return {
            "success": True,
            "path": str(path),
            "error": "",
            "dimensions": [int(width), int(height)],
        }
    except Exception as exc:
        return {
            "success": False,
            "path": str(path),
            "error": str(exc),
            "dimensions": [0, 0],
        }


def _check_xisf_import() -> dict[str, object]:
    try:
        from xisf import XISF  # noqa: F401

        return {"success": True, "error": ""}
    except Exception as exc:
        return {"success": False, "error": str(exc)}


def _check_xisf_file(path: Path) -> dict[str, object]:
    if not path.is_file():
        return {
            "success": False,
            "path": str(path),
            "error": "fixture missing; add packaging/fixtures/smoke_tiny.xisf",
            "dimensions": [0, 0],
            "placeholder": True,
        }
    try:
        _header, width, height = read_header_and_shape(path)
        data = read_photometry_image_data(path)
        if width is None or height is None:
            raise ValueError("XISF header did not expose image dimensions")
        if data.size == 0:
            raise ValueError("XISF image data is empty")
        return {
            "success": True,
            "path": str(path),
            "error": "",
            "dimensions": [int(width), int(height)],
            "data_shape": list(data.shape),
            "placeholder": False,
        }
    except Exception as exc:
        return {
            "success": False,
            "path": str(path),
            "error": str(exc),
            "dimensions": [0, 0],
            "placeholder": False,
        }


def build_packaged_format_smoke_result(*, fixtures_dir: str | Path) -> dict[str, object]:
    if QCoreApplication.instance() is None:
        _app = QCoreApplication([])

    resolved_fixtures_dir = Path(fixtures_dir).resolve()
    qt_result = build_qt_image_format_smoke_result()
    qt_support = qt_result.get("qt_image_support", {})

    png_result = _check_qt_image_file(_fixture_path(resolved_fixtures_dir, "smoke_tiny.png"))
    webp_result = _check_qt_image_file(_fixture_path(resolved_fixtures_dir, "smoke_tiny.webp"))
    fits_result = _check_fits_file(_fixture_path(resolved_fixtures_dir, "smoke_tiny.fits"))
    xisf_import = _check_xisf_import()
    xisf_result = _check_xisf_file(_fixture_path(resolved_fixtures_dir, "smoke_tiny.xisf"))

    success = bool(
        qt_result.get("success")
        and png_result.get("success")
        and webp_result.get("success")
        and fits_result.get("success")
        and xisf_import.get("success")
        and xisf_result.get("success")
    )

    return {
        "success": success,
        "fixtures_dir": str(resolved_fixtures_dir),
        "qt_tiff_lzw": qt_result,
        "png": png_result,
        "webp": webp_result,
        "fits": fits_result,
        "xisf_import": xisf_import,
        "xisf": xisf_result,
        "qt_image_support": qt_support,
    }


def run_packaged_format_smoke(*, fixtures_dir: str | Path, output_path: str | Path) -> dict[str, object]:
    result = build_packaged_format_smoke_result(fixtures_dir=fixtures_dir)
    resolved_output_path = Path(output_path).resolve()
    resolved_output_path.write_text(json.dumps(result, indent=2), encoding="utf-8")
    return result


def about_dialog_content() -> tuple[str, str]:
    from photometry_app.app_metadata import APP_DISPLAY_NAME, APP_VERSION

    return (
        f"About {APP_DISPLAY_NAME}",
        f"Version {APP_VERSION}\n\n"
        "Developed by Ogetay.\n"
        "For more info, please visit: ogetay.com/citizen-astronomy-cast\n\n"
        "Alpha-reviewer build only. Do not distribute.",
    )


def run_about_dialog_smoke() -> dict[str, object]:
    from photometry_app.app_metadata import APP_VERSION
    from PySide6.QtWidgets import QApplication, QMessageBox

    app = QApplication.instance() or QApplication([])
    title, message = about_dialog_content()
    if f"Version {APP_VERSION}" not in message:
        return {"success": False, "error": "application version missing from About dialog text"}
    if "Alpha-reviewer build only. Do not distribute." not in message:
        return {"success": False, "error": "alpha-review notice missing from About dialog text"}

    # Exercise the same QMessageBox construction path without a blocking modal.
    box = QMessageBox(QMessageBox.Icon.Information, title, message)
    box.setModal(False)
    app.processEvents()
    box.close()
    return {"success": True, "error": "", "title": title, "version": APP_VERSION}
