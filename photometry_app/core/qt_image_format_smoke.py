from __future__ import annotations

import base64
import json
from pathlib import Path

import numpy as np
from PySide6.QtCore import QCoreApplication
from PySide6.QtGui import QImage

from photometry_app.core.qt_image_formats import query_qt_image_format_support


EXPECTED_RGB = np.array(
    [
        [[255, 0, 0], [0, 255, 0]],
        [[0, 0, 255], [255, 255, 0]],
    ],
    dtype=np.uint8,
)

SAMPLE_TIFF_LZW_BASE64 = (
    "SUkqABQAAACAP8AQOBQR/wKAgAAKAAABAwABAAAAAgAAAAEBAwABAAAAAgAAAAIBAwADAAAAkgAAAAMB"
    "AwABAAAABQAAAAYBAwABAAAAAgAAABEBBAABAAAACAAAABUBAwABAAAAAwAAABYBAwABAAAAAgAAABcB"
    "BAABAAAACwAAABwBAwABAAAAAQAAAAAAAAAIAAgACAA="
)


def image_to_rgb_array(image: QImage) -> np.ndarray:
    rgb_image = image.convertToFormat(QImage.Format.Format_RGB888)
    width = int(rgb_image.width())
    height = int(rgb_image.height())
    bytes_per_line = int(rgb_image.bytesPerLine())
    buffer = rgb_image.bits()
    array = np.frombuffer(buffer, dtype=np.uint8, count=bytes_per_line * height)
    return array.reshape((height, bytes_per_line))[:, : width * 3].reshape((height, width, 3)).copy()


def build_qt_image_format_smoke_result() -> dict[str, object]:
    if QCoreApplication.instance() is None:
        _app = QCoreApplication([])

    support = query_qt_image_format_support()
    image_bytes = base64.b64decode(SAMPLE_TIFF_LZW_BASE64)
    image = QImage.fromData(image_bytes)

    decode_success = not image.isNull()
    dimensions = [int(image.width()), int(image.height())] if decode_success else [0, 0]
    pixel_match = False
    orientation_flip_detected = False
    channel_swap_detected = False
    decoded_rgb: list[list[list[int]]] | None = None
    error = ""

    if decode_success:
        rgb_array = image_to_rgb_array(image)
        decoded_rgb = rgb_array.tolist()
        pixel_match = bool(np.array_equal(rgb_array, EXPECTED_RGB))
        orientation_flip_detected = bool(np.array_equal(np.flipud(rgb_array), EXPECTED_RGB))
        channel_swap_detected = bool(np.array_equal(rgb_array[..., ::-1], EXPECTED_RGB))
        if tuple(dimensions) != (2, 2):
            error = f"Decoded TIFF dimensions mismatch: expected (2, 2), got {tuple(dimensions)}"
        elif not pixel_match:
            error = "Decoded TIFF RGB pixels do not match the embedded reference sample"
    else:
        error = "Qt failed to decode the embedded TIFF-LZW sample"

    success = bool(
        support.has_tiff
        and support.has_png
        and decode_success
        and tuple(dimensions) == (2, 2)
        and pixel_match
        and not orientation_flip_detected
        and not channel_swap_detected
    )

    return {
        "success": success,
        "decode_success": decode_success,
        "dimensions": dimensions,
        "pixel_match": pixel_match,
        "orientation_flip_detected": orientation_flip_detected,
        "channel_swap_detected": channel_swap_detected,
        "error": error,
        "qt_image_support": support.to_dict(),
        "decoded_rgb": decoded_rgb,
        "expected_rgb": EXPECTED_RGB.tolist(),
    }


def run_qt_image_format_smoke(*, output_path: str | Path) -> dict[str, object]:
    result = build_qt_image_format_smoke_result()
    resolved_output_path = Path(output_path).resolve()
    resolved_output_path.write_text(json.dumps(result, indent=2), encoding="utf-8")
    return result