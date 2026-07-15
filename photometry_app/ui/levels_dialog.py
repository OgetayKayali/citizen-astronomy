from __future__ import annotations

from dataclasses import replace

import numpy as np
from PySide6.QtWidgets import QDialog, QHBoxLayout, QPushButton, QVBoxLayout

from photometry_app.core.plotting import AnnotatedImageDisplay, AnnotatedImageRenderSettings
from photometry_app.ui.curves_widget import HistogramCurvesWidget
from photometry_app.ui.image_view import AnnotatedImageView


_CURVES_PREVIEW_MAX_EDGE = 640
_IDENTITY_CURVE_POINTS = ((0.0, 0.0), (1.0, 1.0))


class CurvesDialog(QDialog):
    def __init__(
        self,
        *,
        display: AnnotatedImageDisplay,
        initial_settings: AnnotatedImageRenderSettings,
        parent=None,
    ) -> None:
        super().__init__(parent)
        self.setWindowTitle("Curves")
        self.setModal(True)
        self.resize(760, 620)

        self._display = _downsample_annotated_display(display)
        self._initial_settings = initial_settings
        self._preview_initialized = False
        self._preview_view = AnnotatedImageView(self)
        self._preview_view.setMinimumHeight(320)
        self._preview_view.setMinimumWidth(500)
        self._curves_widget = HistogramCurvesWidget(self)
        self._curves_widget.curveChanged.connect(self._update_preview)

        histogram_source = self._display.color_preview_normalized if self._display.color_preview_normalized is not None else self._display.preview_normalized
        self._curves_widget.set_histogram_data(histogram_source)
        self._curves_widget.set_curve_points(_initial_curve_points(initial_settings))

        layout = QVBoxLayout()
        layout.addWidget(self._preview_view, stretch=1)
        layout.addWidget(self._curves_widget)

        button_row = QHBoxLayout()
        button_row.addStretch(1)
        reset_button = QPushButton("Reset")
        reset_button.clicked.connect(self._reset_curve)
        cancel_button = QPushButton("Cancel")
        cancel_button.clicked.connect(self.reject)
        apply_button = QPushButton("Apply")
        apply_button.clicked.connect(self.accept)
        button_row.addWidget(reset_button)
        button_row.addWidget(cancel_button)
        button_row.addWidget(apply_button)
        layout.addLayout(button_row)
        self.setLayout(layout)

        self._update_preview()

    def selected_settings(self) -> AnnotatedImageRenderSettings:
        return AnnotatedImageRenderSettings(
            stretch_mode=self._initial_settings.stretch_mode,
            black_point=0.0,
            midtone_point=0.5,
            white_point=1.0,
            curve_points=_stored_curve_points(self._curves_widget.curve_points()),
            brightness=self._initial_settings.brightness,
            contrast=self._initial_settings.contrast,
            inverted=self._initial_settings.inverted,
        )

    def _reset_curve(self) -> None:
        self._curves_widget.reset_curve()

    def _update_preview(self, *_args: object) -> None:
        reset_view = not self._preview_initialized
        self._preview_view.set_content(
            self._display,
            overlays=[],
            grid_overlays=[],
            editor_enabled=False,
            reset_view=reset_view,
            render_settings=self.selected_settings(),
            info_panel=None,
        )
        self._preview_initialized = True


def _initial_curve_points(settings: AnnotatedImageRenderSettings) -> tuple[tuple[float, float], ...]:
    curve_points = tuple(getattr(settings, "curve_points", ()) or ())
    if curve_points:
        return curve_points
    black_point = min(1.0, max(0.0, float(settings.black_point)))
    white_point = min(1.0, max(black_point + 1e-3, float(settings.white_point)))
    midtone_point = min(white_point - 1e-3, max(black_point + 1e-3, float(settings.midtone_point)))
    if abs(black_point - 0.0) <= 1e-6 and abs(midtone_point - 0.5) <= 1e-6 and abs(white_point - 1.0) <= 1e-6:
        return _IDENTITY_CURVE_POINTS
    return ((black_point, 0.0), (midtone_point, 0.5), (white_point, 1.0))


def _stored_curve_points(points: tuple[tuple[float, float], ...]) -> tuple[tuple[float, float], ...]:
    if len(points) == 2:
        first, second = points
        if (
            abs(first[0] - 0.0) <= 1e-6
            and abs(first[1] - 0.0) <= 1e-6
            and abs(second[0] - 1.0) <= 1e-6
            and abs(second[1] - 1.0) <= 1e-6
        ):
            return ()
    return tuple((float(x_value), float(y_value)) for x_value, y_value in points)


def _downsample_annotated_display(display: AnnotatedImageDisplay) -> AnnotatedImageDisplay:
    height, width = display.normalized_data.shape[:2]
    stride = max(1, int(np.ceil(max(height, width) / float(_CURVES_PREVIEW_MAX_EDGE))))
    if stride <= 1:
        return display
    return replace(
        display,
        normalized_data=_downsample_array(display.normalized_data, stride),
        preview_normalized=_downsample_optional_array(display.preview_normalized, stride),
        asinh_preview=_downsample_optional_array(display.asinh_preview, stride),
        linear_preview_normalized=_downsample_optional_array(display.linear_preview_normalized, stride),
        color_preview_normalized=_downsample_optional_array(display.color_preview_normalized, stride),
        color_asinh_preview=_downsample_optional_array(display.color_asinh_preview, stride),
        color_linear_preview_normalized=_downsample_optional_array(display.color_linear_preview_normalized, stride),
    )


def _downsample_optional_array(array: np.ndarray | None, stride: int) -> np.ndarray | None:
    if array is None:
        return None
    return _downsample_array(array, stride)


def _downsample_array(array: np.ndarray, stride: int) -> np.ndarray:
    if array.ndim >= 2:
        return np.ascontiguousarray(array[::stride, ::stride, ...])
    return np.ascontiguousarray(array)


AsteroidLevelsDialog = CurvesDialog