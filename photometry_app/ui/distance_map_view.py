from __future__ import annotations

import math
from dataclasses import replace
from pathlib import Path

import numpy as np
from astropy.wcs import WCS
from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QColor, QFont, QImage, QVector3D
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDialog,
    QDoubleSpinBox,
    QFileDialog,
    QFormLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QMessageBox,
    QPlainTextEdit,
    QPushButton,
    QSpinBox,
    QSplitter,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from photometry_app.core.distance_map import (
    DistanceMapImagingAxes,
    DistanceMapResult,
    DistanceMapStar,
    DISTANCE_MAP_MAX_STAR_COUNT,
    distance_map_depth_ruler_geometry,
    distance_map_imaging_axes,
    distance_map_imaging_axes_from_field,
    distance_map_pixel_position,
    distance_map_reference_magnitude,
    distance_map_star_color_rgba,
    distance_map_star_color_hex,
    distance_map_star_point_size,
    distance_map_tomography_default_depth,
    distance_map_tomography_depth_range,
    distance_map_tomography_plane_transform_from_field,
)
from photometry_app.core.plotting import AnnotatedImageDisplay, build_annotated_image_display, render_annotated_image
from photometry_app.core.distance_map_clusters import (
    DistanceMapClusterResult,
    DistanceMapClusterSettings,
    distance_map_cluster_method_label,
    distance_map_cluster_preset_description,
    distance_map_cluster_preset_label,
    find_distance_map_cluster,
)
from photometry_app.core.distance_map_display import (
    DistanceMapDisplayOptions,
    DistanceMapDisplayResult,
    build_parallax_uncertainty_segments,
    prepare_distance_map_display,
)
from photometry_app.core.image_io import read_header
from photometry_app.core.models import SolvedField
from photometry_app.ui.image_view import AnnotatedImageView, ImageOverlay

try:
    import pyqtgraph as pg
    import pyqtgraph.opengl as gl
    from pyqtgraph import Transform3D
except Exception:
    pg = None
    gl = None
    Transform3D = None


_CLUSTER_GLOW_COLOR = (1.0, 0.82, 0.4, 0.35)
_PARALLAX_UNCERTAINTY_COLOR = (0.82, 0.88, 1.0, 0.42)
_CATALOG_SPHERE_COLOR = (1.0, 0.78, 0.35, 0.88)
_TOMOGRAPHY_TEXTURE_ALPHA = 0.82
_DISTANCE_RULER_COLOR = (0.72, 0.92, 1.0, 0.92)
_DISTANCE_RULER_TICK_COLOR = (0.82, 0.94, 1.0, 0.88)


def _distance_map_tomography_texture(display: AnnotatedImageDisplay) -> np.ndarray | None:
    if pg is None:
        return None
    rendered = render_annotated_image(display)
    if rendered.ndim == 2:
        rgba = pg.makeRGBA(rendered)[0]
    elif rendered.ndim == 3 and rendered.shape[2] in {3, 4}:
        rgba = pg.makeRGBA(rendered[..., :3])[0]
    else:
        return None
    rgba = np.ascontiguousarray(rgba, dtype=np.uint8)
    rgba[..., 3] = np.clip((rgba[..., 3].astype(np.float32) * _TOMOGRAPHY_TEXTURE_ALPHA).astype(np.uint8), 0, 255)
    return rgba


class _DistanceMapGLViewWidget(gl.GLViewWidget if gl is not None else QWidget):
    tomography_depth_delta = Signal(float)

    def __init__(self, parent: QWidget | None = None) -> None:
        if gl is None:
            super().__init__(parent)
            return
        super().__init__(parent)
        self._tomography_drag_enabled = False
        self._tomography_depth_sensitivity_pc_per_pixel = 1.0

    def set_tomography_drag_enabled(self, enabled: bool, *, depth_sensitivity_pc_per_pixel: float) -> None:
        self._tomography_drag_enabled = bool(enabled)
        self._tomography_depth_sensitivity_pc_per_pixel = max(1e-6, float(depth_sensitivity_pc_per_pixel))

    def mouseMoveEvent(self, ev) -> None:  # noqa: ANN001, N802
        if gl is None:
            return
        if (
            self._tomography_drag_enabled
            and ev.buttons() == Qt.MouseButton.LeftButton
            and (ev.modifiers() & Qt.KeyboardModifier.ControlModifier)
        ):
            lpos = ev.position() if hasattr(ev, "position") else ev.localPos()
            if not hasattr(self, "mousePos"):
                self.mousePos = lpos
            diff = lpos - self.mousePos
            self.mousePos = lpos
            depth_delta = -float(diff.y()) * self._tomography_depth_sensitivity_pc_per_pixel
            if abs(depth_delta) > 0.0:
                self.tomography_depth_delta.emit(depth_delta)
            return
        super().mouseMoveEvent(ev)


class DistanceMapClusterDialog(QDialog):
    def __init__(self, settings: DistanceMapClusterSettings, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        normalized = settings.normalized()
        self.setWindowTitle("Distance Map Cluster Detection")
        self.resize(470, 390)
        self._updating_inputs = False

        description = QLabel(
            "Find likely open-cluster members from Gaia proper motion and parallax, then mark them on the 3D map and field image."
        )
        description.setWordWrap(True)

        self._preset_input = QComboBox()
        self._preset_input.addItem("Default", "default")
        self._preset_input.addItem("Tight", "tight")
        self._preset_input.addItem("Loose", "loose")
        self._preset_input.addItem("Parallax Priority", "parallax")
        self._preset_input.addItem("Custom", "custom")
        self._preset_input.currentIndexChanged.connect(self._handle_preset_changed)

        self._preset_description = QLabel()
        self._preset_description.setWordWrap(True)

        self._auto_filter_input = QCheckBox("Turn on Only Group automatically after detection")

        general_form = QFormLayout()
        general_form.addRow("Preset", self._preset_input)

        self._expert_toggle_button = QPushButton("Show Expert Controls")
        self._expert_toggle_button.setCheckable(True)
        self._expert_toggle_button.toggled.connect(self._set_expert_controls_visible)

        expert_note = QLabel("Changing any expert setting switches the preset to Custom.")
        expert_note.setWordWrap(True)

        self._method_input = QComboBox()
        self._method_input.addItem("Auto (recommended)", "auto")
        self._method_input.addItem("Lightweight", "lightweight")
        self._method_input.addItem("Sklearn DBSCAN", "sklearn")
        self._method_input.currentIndexChanged.connect(self._mark_custom_from_expert_change)

        self._strictness_input = QDoubleSpinBox()
        self._strictness_input.setRange(0.4, 2.5)
        self._strictness_input.setSingleStep(0.1)
        self._strictness_input.setDecimals(2)
        self._strictness_input.valueChanged.connect(self._mark_custom_from_expert_change)

        self._parallax_mode_input = QComboBox()
        self._parallax_mode_input.addItem("Auto", "auto")
        self._parallax_mode_input.addItem("Require parallax", "always")
        self._parallax_mode_input.addItem("Proper motion only", "never")
        self._parallax_mode_input.currentIndexChanged.connect(self._mark_custom_from_expert_change)

        self._refine_magnitude_input = QCheckBox("Apply magnitude/color cleanup")
        self._refine_magnitude_input.toggled.connect(self._mark_custom_from_expert_change)

        expert_form = QFormLayout()
        expert_form.addRow("Backend", self._method_input)
        expert_form.addRow("Strictness", self._strictness_input)
        expert_form.addRow("Parallax", self._parallax_mode_input)
        expert_form.addRow(self._refine_magnitude_input)

        self._expert_container = QWidget()
        expert_layout = QVBoxLayout()
        expert_layout.setContentsMargins(0, 0, 0, 0)
        expert_layout.addWidget(expert_note)
        expert_layout.addLayout(expert_form)
        self._expert_container.setLayout(expert_layout)

        button_row = QHBoxLayout()
        button_row.addStretch(1)
        cancel_button = QPushButton("Cancel")
        cancel_button.clicked.connect(self.reject)
        ok_button = QPushButton("OK")
        ok_button.clicked.connect(self.accept)
        button_row.addWidget(cancel_button)
        button_row.addWidget(ok_button)

        layout = QVBoxLayout()
        layout.addWidget(description)
        layout.addLayout(general_form)
        layout.addWidget(self._preset_description)
        layout.addWidget(self._auto_filter_input)
        layout.addWidget(self._expert_toggle_button)
        layout.addWidget(self._expert_container)
        layout.addStretch(1)
        layout.addLayout(button_row)
        self.setLayout(layout)

        self._apply_settings(normalized)
        self._expert_toggle_button.setChecked(normalized.preset == "custom")

    def build_settings(self) -> DistanceMapClusterSettings:
        return DistanceMapClusterSettings(
            preset=str(self._preset_input.currentData() or "default"),
            method=str(self._method_input.currentData() or "auto"),
            strictness=float(self._strictness_input.value()),
            parallax_mode=str(self._parallax_mode_input.currentData() or "auto"),
            refine_magnitude_consistency=self._refine_magnitude_input.isChecked(),
            auto_filter=self._auto_filter_input.isChecked(),
        ).normalized()

    def _apply_settings(self, settings: DistanceMapClusterSettings) -> None:
        self._updating_inputs = True
        preset_index = self._preset_input.findData(settings.preset)
        self._preset_input.setCurrentIndex(0 if preset_index < 0 else preset_index)
        method_index = self._method_input.findData(settings.method)
        self._method_input.setCurrentIndex(0 if method_index < 0 else method_index)
        self._strictness_input.setValue(settings.strictness)
        parallax_index = self._parallax_mode_input.findData(settings.parallax_mode)
        self._parallax_mode_input.setCurrentIndex(0 if parallax_index < 0 else parallax_index)
        self._refine_magnitude_input.setChecked(settings.refine_magnitude_consistency)
        self._auto_filter_input.setChecked(settings.auto_filter)
        self._updating_inputs = False
        self._update_preset_description()

    def _handle_preset_changed(self) -> None:
        if self._updating_inputs:
            return
        preset = str(self._preset_input.currentData() or "default")
        if preset != "custom":
            preset_settings = DistanceMapClusterSettings(
                preset=preset,
                auto_filter=self._auto_filter_input.isChecked(),
            ).normalized()
            self._apply_settings(preset_settings)
        else:
            self._update_preset_description()
            self._expert_toggle_button.setChecked(True)

    def _mark_custom_from_expert_change(self) -> None:
        if self._updating_inputs:
            return
        if str(self._preset_input.currentData() or "default") != "custom":
            self._updating_inputs = True
            custom_index = self._preset_input.findData("custom")
            self._preset_input.setCurrentIndex(0 if custom_index < 0 else custom_index)
            self._updating_inputs = False
        self._update_preset_description()

    def _update_preset_description(self) -> None:
        preset = str(self._preset_input.currentData() or "default")
        self._preset_description.setText(distance_map_cluster_preset_description(preset))

    def _set_expert_controls_visible(self, visible: bool) -> None:
        self._expert_container.setVisible(visible)
        self._expert_toggle_button.setText("Hide Expert Controls" if visible else "Show Expert Controls")


class DistanceMap3DWidget(QWidget):
    """Independent 3D star-map view for Distance Map mode."""

    tomography_depth_changed = Signal(float)

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._stars: tuple[DistanceMapStar, ...] = ()
        self._cluster_member_indices: frozenset[int] = frozenset()
        self._only_cluster = False
        self._reference_magnitude = 13.0
        self._camera_mode = "overview"
        self._show_labels = True
        self._show_distance_ruler = False
        self._imaging_axes: DistanceMapImagingAxes | None = None
        self._gl_view: _DistanceMapGLViewWidget | None = None
        self._gl_panel_container: QWidget | None = None
        self._gl_panel_layout: QVBoxLayout | None = None
        self._gl_scene_items: list[object] = []
        self._observer_items: list[object] = []
        self._tomography_enabled = False
        self._tomography_texture: np.ndarray | None = None
        self._tomography_axes: DistanceMapImagingAxes | None = None
        self._tomography_depth_pc = 0.0
        self._tomography_depth_min_pc = 0.0
        self._tomography_depth_max_pc = 100.0
        self._tomography_image_item: object | None = None
        self._tomography_solved_field = None
        self._uncertainty_segments: np.ndarray | None = None
        self._sphere_wireframe: tuple[np.ndarray, ...] | None = None

        self._gl_panel_container = QWidget(self)
        self._gl_panel_layout = QVBoxLayout()
        self._gl_panel_layout.setContentsMargins(0, 0, 0, 0)
        self._gl_panel_container.setLayout(self._gl_panel_layout)
        self._gl_fallback_label = QLabel("OpenGL 3D view is unavailable in this environment.", self._gl_panel_container)
        self._gl_fallback_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._gl_fallback_label.setWordWrap(True)
        self._gl_panel_layout.addWidget(self._gl_fallback_label)

        layout = QVBoxLayout()
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)
        layout.addWidget(self._gl_panel_container, stretch=1)
        self.setLayout(layout)

    def showEvent(self, event) -> None:  # noqa: ANN001, N802
        super().showEvent(event)
        if gl is not None and self._gl_view is None:
            self._recreate_gl_view()

    def set_stars(
        self,
        stars: tuple[DistanceMapStar, ...],
        *,
        cluster_member_indices: frozenset[int] | None = None,
        only_cluster: bool = False,
        uncertainty_segments: np.ndarray | None = None,
        sphere_wireframe: tuple[np.ndarray, ...] | None = None,
    ) -> None:
        self._stars = tuple(stars)
        self._cluster_member_indices = frozenset() if cluster_member_indices is None else frozenset(cluster_member_indices)
        self._only_cluster = bool(only_cluster)
        self._uncertainty_segments = None if uncertainty_segments is None else np.ascontiguousarray(uncertainty_segments, dtype=float)
        self._sphere_wireframe = sphere_wireframe
        self._reference_magnitude = distance_map_reference_magnitude(self._stars)
        if gl is None:
            self._gl_fallback_label.setText(
                "OpenGL 3D view is unavailable in this environment."
                if not stars
                else f"{len(stars)} star(s) loaded, but OpenGL 3D view is unavailable."
            )
            return
        if self._gl_view is None:
            self._recreate_gl_view()
        else:
            self._rebuild_scene()

    def clear(self) -> None:
        self._imaging_axes = None
        self._uncertainty_segments = None
        self._sphere_wireframe = None
        self.set_stars((), cluster_member_indices=frozenset(), only_cluster=False)

    def set_camera_mode(self, mode: str) -> None:
        self._camera_mode = str(mode or "overview")
        self._apply_camera_mode()

    def set_show_labels(self, show: bool) -> None:
        self._show_labels = bool(show)
        self._rebuild_scene()

    def set_show_distance_ruler(self, show: bool) -> None:
        self._show_distance_ruler = bool(show)
        self._rebuild_scene()

    def configure_imaging_axes(self, imaging_axes: DistanceMapImagingAxes | None) -> None:
        self._imaging_axes = imaging_axes
        if self._show_distance_ruler:
            self._rebuild_scene()

    def reset_camera_view(self) -> None:
        self._apply_camera_mode()

    def capture_view_image(self) -> QImage | None:
        if self._gl_view is None:
            return None
        image: QImage | None = None
        read_qimage = getattr(self._gl_view, "readQImage", None)
        if callable(read_qimage):
            image = read_qimage()
        elif hasattr(self._gl_view, "grabFramebuffer"):
            image = self._gl_view.grabFramebuffer()
        else:
            image = self._gl_view.grab().toImage()
        if image is None or image.isNull():
            return None
        return image

    def set_tomography_enabled(self, enabled: bool) -> None:
        self._tomography_enabled = bool(enabled)
        if not enabled:
            self._remove_tomography_item()
        else:
            self._sync_tomography_plane()
        self._sync_tomography_drag()

    def tomography_enabled(self) -> bool:
        return self._tomography_enabled

    def configure_tomography(
        self,
        *,
        texture: np.ndarray | None,
        imaging_axes: DistanceMapImagingAxes | None,
        solved_field: SolvedField | None,
        depth_pc: float | None,
        depth_min_pc: float | None,
        depth_max_pc: float | None,
    ) -> None:
        self._tomography_texture = None if texture is None else np.ascontiguousarray(texture, dtype=np.uint8)
        self._tomography_axes = imaging_axes
        self._tomography_solved_field = solved_field
        if depth_pc is not None:
            self._tomography_depth_pc = float(depth_pc)
        if depth_min_pc is not None:
            self._tomography_depth_min_pc = float(depth_min_pc)
        if depth_max_pc is not None:
            self._tomography_depth_max_pc = float(depth_max_pc)
        if self._tomography_enabled:
            self._sync_tomography_plane()
            self._sync_tomography_drag()

    def clear_tomography(self) -> None:
        self._tomography_enabled = False
        self._tomography_texture = None
        self._tomography_axes = None
        self._tomography_solved_field = None
        self._remove_tomography_item()
        self._sync_tomography_drag()

    def _sync_tomography_drag(self) -> None:
        if self._gl_view is None or gl is None:
            return
        if not self._tomography_enabled:
            self._gl_view.set_tomography_drag_enabled(False, depth_sensitivity_pc_per_pixel=1.0)
            return
        depth_span = max(1.0, self._tomography_depth_max_pc - self._tomography_depth_min_pc)
        widget_height = max(120, self._gl_view.height())
        sensitivity = depth_span / float(widget_height)
        self._gl_view.set_tomography_drag_enabled(True, depth_sensitivity_pc_per_pixel=sensitivity)

    def _handle_tomography_depth_delta(self, depth_delta_pc: float) -> None:
        if not self._tomography_enabled:
            return
        depth_value = float(self._tomography_depth_pc) + float(depth_delta_pc)
        depth_value = max(self._tomography_depth_min_pc, min(self._tomography_depth_max_pc, depth_value))
        if abs(depth_value - self._tomography_depth_pc) <= 1e-9:
            return
        self._tomography_depth_pc = depth_value
        self._sync_tomography_plane()
        self.tomography_depth_changed.emit(depth_value)
        if self._camera_mode == "tomography-face":
            self._apply_camera_mode()

    def _remove_tomography_item(self) -> None:
        if self._gl_view is None or self._tomography_image_item is None:
            self._tomography_image_item = None
            return
        try:
            self._gl_view.removeItem(self._tomography_image_item)
        except Exception:
            pass
        self._tomography_image_item = None

    def _sync_tomography_plane(self) -> None:
        if gl is None or Transform3D is None or self._gl_view is None:
            return
        if not self._tomography_enabled or self._tomography_texture is None or self._tomography_solved_field is None:
            self._remove_tomography_item()
            return
        texture = self._tomography_texture
        if texture.ndim != 3 or texture.shape[2] != 4:
            self._remove_tomography_item()
            return
        if self._tomography_solved_field is None:
            self._remove_tomography_item()
            return
        try:
            wcs = WCS(read_header(self._tomography_solved_field.wcs_path))
        except Exception:
            self._remove_tomography_item()
            return
        transform_matrix, imaging_axes = distance_map_tomography_plane_transform_from_field(
            self._tomography_solved_field,
            wcs,
            self._tomography_depth_pc,
            texture_shape=(int(texture.shape[0]), int(texture.shape[1])),
        )
        self._tomography_axes = imaging_axes
        if self._tomography_image_item is None:
            image_item = gl.GLImageItem(texture, smooth=True, glOptions="translucent")
            image_item.setDepthValue(-10)
            self._gl_view.addItem(image_item)
            self._tomography_image_item = image_item
        else:
            self._tomography_image_item.setData(texture)
        self._tomography_image_item.setTransform(Transform3D(transform_matrix))
        self._gl_view.update()

    @staticmethod
    def _reset_gl_shader_program_caches() -> None:
        if gl is None:
            return
        for item_name in ("GLScatterPlotItem", "GLLinePlotItem"):
            item_class = getattr(gl, item_name, None)
            if item_class is not None and hasattr(item_class, "_shaderProgram"):
                setattr(item_class, "_shaderProgram", None)

    def _recreate_gl_view(self) -> None:
        if gl is None or self._gl_panel_container is None or self._gl_panel_layout is None:
            return
        self._clear_scene()
        if self._gl_view is not None:
            self._gl_panel_layout.removeWidget(self._gl_view)
            self._gl_view.deleteLater()
            self._gl_view = None
        self._tomography_image_item = None
        self._gl_fallback_label.hide()
        self._reset_gl_shader_program_caches()
        self._gl_view = _DistanceMapGLViewWidget(self._gl_panel_container)
        self._gl_view.setBackgroundColor(QColor("#040713"))
        self._gl_view.setMinimumHeight(280)
        self._gl_view.opts["fov"] = 58
        self._gl_view.opts["distance"] = 6.0
        self._gl_view.tomography_depth_delta.connect(self._handle_tomography_depth_delta)
        self._gl_panel_layout.addWidget(self._gl_view, stretch=1)
        self._rebuild_scene()
        self._apply_camera_mode()
        self._sync_tomography_drag()

    def resizeEvent(self, event) -> None:  # noqa: ANN001, N802
        super().resizeEvent(event)
        self._sync_tomography_drag()

    def _clear_scene(self) -> None:
        if self._gl_view is None:
            self._gl_scene_items = []
            self._observer_items = []
            return
        for item in self._gl_scene_items:
            try:
                self._gl_view.removeItem(item)
            except Exception:
                continue
        self._gl_scene_items = []
        self._observer_items = []

    def _visible_star_entries(self) -> list[tuple[int, DistanceMapStar]]:
        if self._only_cluster and self._cluster_member_indices:
            return [(index, self._stars[index]) for index in sorted(self._cluster_member_indices) if 0 <= index < len(self._stars)]
        return list(enumerate(self._stars))

    def _scene_extent(self) -> float:
        entries = self._visible_star_entries()
        if not entries:
            return 10.0
        positions = np.asarray([(star.x_pc, star.y_pc, star.z_pc) for _, star in entries], dtype=float)
        norms = np.linalg.norm(positions, axis=1)
        return max(5.0, float(np.max(norms)) * 1.15)

    def _field_center(self) -> QVector3D:
        entries = self._visible_star_entries()
        if not entries:
            return QVector3D(0.0, 0.0, 0.0)
        if self._cluster_member_indices:
            cluster_entries = [(index, star) for index, star in entries if index in self._cluster_member_indices]
            if cluster_entries:
                entries = cluster_entries
        positions = np.asarray([(star.x_pc, star.y_pc, star.z_pc) for _, star in entries], dtype=float)
        center = np.mean(positions, axis=0)
        return QVector3D(float(center[0]), float(center[1]), float(center[2]))

    def _star_color(self, star: DistanceMapStar) -> tuple[float, float, float, float]:
        return distance_map_star_color_rgba(star)

    def _star_size(self, star: DistanceMapStar, *, is_cluster_member: bool) -> float:
        base_size = distance_map_star_point_size(star, reference_magnitude=self._reference_magnitude)
        if is_cluster_member:
            return max(base_size + 2.0, 7.0)
        return base_size

    def _active_imaging_axes(self) -> DistanceMapImagingAxes | None:
        if self._tomography_axes is not None:
            return self._tomography_axes
        return self._imaging_axes

    def _add_distance_ruler(self, stars: tuple[DistanceMapStar, ...]) -> None:
        if gl is None or self._gl_view is None or not self._show_distance_ruler or not stars:
            return
        imaging_axes = self._active_imaging_axes()
        if imaging_axes is None:
            return
        geometry = distance_map_depth_ruler_geometry(stars, imaging_axes)
        if geometry is None:
            return
        axis_line = gl.GLLinePlotItem(
            pos=geometry.axis_points,
            color=_DISTANCE_RULER_COLOR,
            width=2.4,
            antialias=True,
            mode="line_strip",
        )
        self._gl_scene_items.append(axis_line)
        self._gl_view.addItem(axis_line)
        tick_positions = geometry.tick_segments.reshape(-1, 3)
        tick_line = gl.GLLinePlotItem(
            pos=tick_positions,
            color=_DISTANCE_RULER_TICK_COLOR,
            width=1.8,
            antialias=True,
            mode="lines",
        )
        self._gl_scene_items.append(tick_line)
        self._gl_view.addItem(tick_line)
        label_class = getattr(gl, "GLTextItem", None)
        if label_class is None:
            return
        label_font = QFont("Segoe UI", 8)
        label_color = QColor(184, 228, 255)
        for position, text in geometry.tick_labels:
            label_item = label_class(
                pos=np.asarray(position, dtype=float),
                text=text,
                color=label_color,
                font=label_font,
            )
            self._gl_scene_items.append(label_item)
            self._gl_view.addItem(label_item)

    def _rebuild_scene(self) -> None:
        if self._gl_view is None:
            return
        self._clear_scene()
        entries = self._visible_star_entries()
        if not entries:
            return

        field_entries = [(index, star) for index, star in entries if index not in self._cluster_member_indices]
        cluster_entries = [(index, star) for index, star in entries if index in self._cluster_member_indices]

        if field_entries:
            positions = np.asarray([(star.x_pc, star.y_pc, star.z_pc) for _, star in field_entries], dtype=float)
            colors = np.asarray([self._star_color(star) for _, star in field_entries], dtype=float)
            sizes = np.asarray([self._star_size(star, is_cluster_member=False) for _, star in field_entries], dtype=float)
            scatter_item = gl.GLScatterPlotItem(pos=positions, color=colors, size=sizes, pxMode=True)
            self._gl_scene_items.append(scatter_item)
            self._gl_view.addItem(scatter_item)

        if cluster_entries:
            positions = np.asarray([(star.x_pc, star.y_pc, star.z_pc) for _, star in cluster_entries], dtype=float)
            colors = np.asarray([self._star_color(star) for _, star in cluster_entries], dtype=float)
            sizes = np.asarray([self._star_size(star, is_cluster_member=True) for _, star in cluster_entries], dtype=float)
            cluster_scatter = gl.GLScatterPlotItem(pos=positions, color=colors, size=sizes, pxMode=True)
            self._gl_scene_items.append(cluster_scatter)
            self._gl_view.addItem(cluster_scatter)

            glow_sizes = sizes + 4.0
            cluster_glow = gl.GLScatterPlotItem(
                pos=positions,
                color=np.asarray([_CLUSTER_GLOW_COLOR for _ in cluster_entries], dtype=float),
                size=glow_sizes,
                pxMode=True,
            )
            self._gl_scene_items.append(cluster_glow)
            self._gl_view.addItem(cluster_glow)

        observer_glow = gl.GLScatterPlotItem(
            pos=np.array([[0.0, 0.0, 0.0]], dtype=float),
            color=np.array([[0.45, 0.82, 1.0, 0.18]], dtype=float),
            size=28.0,
            pxMode=True,
        )
        observer_core = gl.GLScatterPlotItem(
            pos=np.array([[0.0, 0.0, 0.0]], dtype=float),
            color=np.array([[0.55, 0.90, 1.0, 0.98]], dtype=float),
            size=12.0,
            pxMode=True,
        )
        self._observer_items.extend([observer_glow, observer_core])
        self._gl_scene_items.extend(self._observer_items)
        for item in self._observer_items:
            self._gl_view.addItem(item)

        if self._show_labels:
            label_class = getattr(gl, "GLTextItem", None)
            if label_class is not None:
                label_font = QFont("Segoe UI", 8)
                label_entries = cluster_entries if cluster_entries else entries
                for _, star in label_entries[:40]:
                    red, green, blue, _alpha = distance_map_star_color_rgba(star)
                    label_color = QColor(
                        int(round(red * 255.0)),
                        int(round(green * 255.0)),
                        int(round(blue * 255.0)),
                    )
                    label_item = label_class(
                        pos=np.array((star.x_pc, star.y_pc, star.z_pc), dtype=float),
                        text=star.name,
                        color=label_color,
                        font=label_font,
                    )
                    self._gl_scene_items.append(label_item)
                    self._gl_view.addItem(label_item)

        visible_stars = tuple(star for _, star in entries)
        self._add_distance_ruler(visible_stars)
        self._add_parallax_uncertainty_segments()
        self._add_catalog_sphere_wireframe()

        self._apply_camera_mode()

    def _add_parallax_uncertainty_segments(self) -> None:
        if gl is None or self._gl_view is None or self._uncertainty_segments is None:
            return
        if self._uncertainty_segments.ndim != 2 or self._uncertainty_segments.shape[1] != 3:
            return
        if self._uncertainty_segments.shape[0] < 2:
            return
        uncertainty_line = gl.GLLinePlotItem(
            pos=self._uncertainty_segments,
            color=_PARALLAX_UNCERTAINTY_COLOR,
            width=1.4,
            antialias=True,
            mode="lines",
        )
        self._gl_scene_items.append(uncertainty_line)
        self._gl_view.addItem(uncertainty_line)

    def _add_catalog_sphere_wireframe(self) -> None:
        if gl is None or self._gl_view is None or not self._sphere_wireframe:
            return
        for circle_points in self._sphere_wireframe:
            if circle_points.ndim != 2 or circle_points.shape[1] != 3 or circle_points.shape[0] < 2:
                continue
            sphere_line = gl.GLLinePlotItem(
                pos=circle_points,
                color=_CATALOG_SPHERE_COLOR,
                width=2.0,
                antialias=True,
                mode="line_strip",
            )
            self._gl_scene_items.append(sphere_line)
            self._gl_view.addItem(sphere_line)

    def _apply_camera_mode(self) -> None:
        if self._gl_view is None:
            return
        scene_extent = self._scene_extent()
        target = self._field_center()
        mode = self._camera_mode
        if mode == "topdown":
            self._gl_view.setCameraPosition(pos=target, distance=scene_extent * 2.4, elevation=90.0, azimuth=-90.0)
        elif mode == "side":
            self._gl_view.setCameraPosition(pos=target, distance=scene_extent * 2.2, elevation=8.0, azimuth=0.0)
        elif mode == "field-center":
            self._gl_view.setCameraPosition(pos=target, distance=max(5.0, scene_extent * 0.9), elevation=18.0, azimuth=-35.0)
        elif mode == "tomography-face" and self._tomography_axes is not None:
            los = np.asarray(self._tomography_axes.line_of_sight, dtype=float)
            depth_pc = max(1.0, float(self._tomography_depth_pc))
            plane_center = los * depth_pc
            target = QVector3D(float(plane_center[0]), float(plane_center[1]), float(plane_center[2]))
            view_distance = max(5.0, depth_pc * 1.02)
            horizontal = math.hypot(float(los[0]), float(los[1]))
            elevation = math.degrees(math.atan2(float(los[2]), horizontal))
            azimuth = math.degrees(math.atan2(float(los[1]), float(los[0])))
            self._gl_view.setCameraPosition(
                pos=target,
                distance=view_distance,
                elevation=elevation,
                azimuth=azimuth,
            )
        else:
            self._gl_view.setCameraPosition(pos=target, distance=scene_extent * 2.1, elevation=24.0, azimuth=-58.0)


class DistanceMapPanel(QWidget):
    open_image_requested = Signal()
    apply_limits_requested = Signal()
    cluster_settings_changed = Signal(object)
    display_options_changed = Signal(object)

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._current_result: DistanceMapResult | None = None
        self._source_image_path: Path | None = None
        self._cluster_settings = DistanceMapClusterSettings().normalized()
        self._cluster_member_indices: frozenset[int] = frozenset()
        self._display_member_indices: frozenset[int] = frozenset()
        self._cluster_result: DistanceMapClusterResult | None = None
        self._image_display_cache: dict[str, object] = {}
        self._display_options = DistanceMapDisplayOptions().normalized()

        self._open_button = QPushButton("Open", self)
        self._open_button.setToolTip("Open a source image and automatically build the 3D distance map from catalog stars.")
        self._open_button.clicked.connect(self.open_image_requested.emit)

        self._find_cluster_button = QPushButton("Find Cluster", self)
        self._find_cluster_button.setToolTip("Search the current map for a likely moving cluster and highlight its members.")
        self._find_cluster_button.clicked.connect(self._handle_find_cluster_clicked)

        self._cluster_advanced_button = QPushButton("Advanced...", self)
        self._cluster_advanced_button.setToolTip("Adjust cluster detection presets and optional expert controls.")
        self._cluster_advanced_button.clicked.connect(self._open_cluster_dialog)

        self._only_cluster_checkbox = QCheckBox("Only Group", self)
        self._only_cluster_checkbox.setToolTip("Show only the highlighted cluster members in the 3D map, table, and field image.")
        self._only_cluster_checkbox.setEnabled(False)
        self._only_cluster_checkbox.toggled.connect(self._handle_display_filters_changed)

        self._model_cluster_depth_checkbox = QCheckBox("Model Cluster Depth", self)
        self._model_cluster_depth_checkbox.setToolTip(
            "After moving-group detection, place members inside a toy globular-cluster sphere "
            "with higher density at the center. Uses Find Cluster when set, otherwise auto-detects "
            "members from proper motion and parallax only."
        )
        self._model_cluster_depth_checkbox.setChecked(True)
        self._model_cluster_depth_checkbox.toggled.connect(self._handle_display_options_changed)

        self._parallax_uncertainty_checkbox = QCheckBox("Parallax Uncertainty", self)
        self._parallax_uncertainty_checkbox.setToolTip(
            "Draw depth intervals along each star's line of sight to show Gaia parallax uncertainty."
        )
        self._parallax_uncertainty_checkbox.toggled.connect(self._handle_display_options_changed)

        self._catalog_sphere_checkbox = QCheckBox("Catalog Sphere", self)
        self._catalog_sphere_checkbox.setToolTip(
            "For named globular clusters, overlay a catalog distance/radius sphere. "
            "Uses external cluster distances rather than Gaia parallax alone."
        )
        self._catalog_sphere_checkbox.toggled.connect(self._handle_display_options_changed)

        self._max_magnitude_spin = QDoubleSpinBox(self)
        self._max_magnitude_spin.setRange(1.0, 22.0)
        self._max_magnitude_spin.setDecimals(1)
        self._max_magnitude_spin.setSingleStep(0.5)
        self._max_magnitude_spin.setValue(17.0)
        self._max_magnitude_spin.setSuffix(" mag")

        self._max_distance_spin = QDoubleSpinBox(self)
        self._max_distance_spin.setRange(1.0, 50000.0)
        self._max_distance_spin.setDecimals(1)
        self._max_distance_spin.setSingleStep(50.0)
        self._max_distance_spin.setValue(500.0)
        self._max_distance_spin.setSuffix(" pc")

        self._max_stars_spin = QSpinBox(self)
        self._max_stars_spin.setRange(10, DISTANCE_MAP_MAX_STAR_COUNT)
        self._max_stars_spin.setSingleStep(50)
        self._max_stars_spin.setValue(500)
        self._max_stars_spin.setToolTip(
            "Maximum stars drawn in the 3D map after parallax and distance filtering. "
            "Gaia is queried with up to 4x this many rows so brighter candidates are available."
        )

        self._min_parallax_snr_spin = QDoubleSpinBox(self)
        self._min_parallax_snr_spin.setRange(0.0, 50.0)
        self._min_parallax_snr_spin.setDecimals(1)
        self._min_parallax_snr_spin.setSingleStep(0.5)
        self._min_parallax_snr_spin.setValue(5.0)
        self._min_parallax_snr_spin.setToolTip(
            "Require Gaia parallax SNR (Plx / e_Plx) at or above this value. "
            "Set to 0 to disable the quality cut."
        )

        self._apply_limits_button = QPushButton("Apply", self)
        self._apply_limits_button.setToolTip(
            "Re-query Gaia and rebuild the distance map using the current magnitude, distance, and star limits."
        )
        self._apply_limits_button.clicked.connect(self.apply_limits_requested.emit)
        self._apply_limits_button.setEnabled(False)

        self._map_view = DistanceMap3DWidget(self)
        self._map_view.tomography_depth_changed.connect(self._handle_tomography_depth_changed)

        self._camera_mode_combo = QComboBox(self)
        self._camera_mode_combo.addItem("Orbit Overview", "overview")
        self._camera_mode_combo.addItem("Top-Down", "topdown")
        self._camera_mode_combo.addItem("Side View", "side")
        self._camera_mode_combo.addItem("Field Center", "field-center")
        self._camera_mode_combo.addItem("Tomography Face-On", "tomography-face")
        self._camera_mode_combo.currentIndexChanged.connect(self._handle_camera_mode_changed)

        self._show_labels_checkbox = QCheckBox("Labels", self)
        self._show_labels_checkbox.setChecked(True)
        self._show_labels_checkbox.toggled.connect(self._handle_show_labels_toggled)

        self._distance_ruler_checkbox = QCheckBox("Distance Ruler", self)
        self._distance_ruler_checkbox.setToolTip(
            "Show a depth scale along the line of sight through the current star distribution."
        )
        self._distance_ruler_checkbox.toggled.connect(self._handle_distance_ruler_toggled)

        self._reset_view_button = QPushButton("Reset View", self)
        self._reset_view_button.clicked.connect(self._handle_reset_camera_view)

        self._save_button = QPushButton("Save", self)
        self._save_button.setToolTip("Save the current 3D distance map view as an image.")
        self._save_button.clicked.connect(self._handle_save_view_image)

        self._tomography_button = QPushButton("Tomography", self)
        self._tomography_button.setCheckable(True)
        self._tomography_button.setToolTip(
            "Overlay the field image as a slice along the line of sight. "
            "Ctrl+left-drag in the 3D view to move the slice depth."
        )
        self._tomography_button.toggled.connect(self._handle_tomography_toggled)

        self._field_image_view = AnnotatedImageView(self)
        self._field_image_view.set_message("Open an image to preview the field.")
        self._field_image_view.setMinimumWidth(220)

        self._results_table = QTableWidget(0, 5, self)
        self._results_table.setHorizontalHeaderLabels(["Name", "G mag", "Parallax", "Distance (pc)", "RA / Dec"])
        self._results_table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        self._results_table.setSelectionMode(QTableWidget.SelectionMode.SingleSelection)
        self._results_table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self._results_table.verticalHeader().setVisible(False)

        self._log_output = QPlainTextEdit(self)
        self._log_output.setReadOnly(True)
        self._log_output.setPlaceholderText("Distance Map progress and summary notes will appear here.")
        self._log_output.setMaximumHeight(120)

        controls_row = QHBoxLayout()
        controls_row.setSpacing(8)
        controls_row.addWidget(self._open_button)
        controls_row.addWidget(self._find_cluster_button)
        controls_row.addWidget(self._cluster_advanced_button)
        controls_row.addWidget(self._only_cluster_checkbox)
        controls_row.addWidget(self._model_cluster_depth_checkbox)
        controls_row.addWidget(self._parallax_uncertainty_checkbox)
        controls_row.addWidget(self._catalog_sphere_checkbox)
        controls_row.addStretch(1)
        controls_row.addWidget(QLabel("View", self))
        controls_row.addWidget(self._camera_mode_combo)
        controls_row.addWidget(self._show_labels_checkbox)
        controls_row.addWidget(self._distance_ruler_checkbox)
        controls_row.addWidget(self._tomography_button)
        controls_row.addWidget(self._reset_view_button)
        controls_row.addWidget(self._save_button)
        controls_row.addStretch(1)
        controls_row.addWidget(QLabel("Mag Limit", self))
        controls_row.addWidget(self._max_magnitude_spin)
        controls_row.addWidget(QLabel("Distance Limit", self))
        controls_row.addWidget(self._max_distance_spin)
        controls_row.addWidget(QLabel("Max Stars", self))
        controls_row.addWidget(self._max_stars_spin)
        controls_row.addWidget(QLabel("Min SNR", self))
        controls_row.addWidget(self._min_parallax_snr_spin)
        controls_row.addWidget(self._apply_limits_button)

        self._map_group = QGroupBox("3D Distance Map", self)
        self._map_group.setObjectName("distanceMapViewGroup")
        map_layout = QVBoxLayout()
        map_layout.addWidget(self._map_view, stretch=1)
        self._map_group.setLayout(map_layout)

        self._field_image_group = QGroupBox("Field Image", self)
        self._field_image_group.setObjectName("distanceMapFieldImageGroup")
        field_image_layout = QVBoxLayout()
        field_image_layout.addWidget(self._field_image_view, stretch=1)
        self._field_image_group.setLayout(field_image_layout)

        self._results_group = QGroupBox("Catalog Stars", self)
        self._results_group.setObjectName("distanceMapResultsGroup")
        results_layout = QVBoxLayout()
        results_layout.addWidget(self._results_table)
        self._results_group.setLayout(results_layout)

        self._log_group = QGroupBox("Work Log", self)
        self._log_group.setObjectName("distanceMapWorkLogGroup")
        log_layout = QVBoxLayout()
        log_layout.addWidget(self._log_output)
        self._log_group.setLayout(log_layout)

        self._right_splitter = QSplitter(Qt.Orientation.Vertical, self)
        self._right_splitter.setChildrenCollapsible(False)
        self._right_splitter.addWidget(self._results_group)
        self._right_splitter.addWidget(self._log_group)
        self._right_splitter.setStretchFactor(0, 5)
        self._right_splitter.setStretchFactor(1, 1)
        self._right_splitter.setSizes([520, 110])

        self._main_splitter = QSplitter(Qt.Orientation.Horizontal, self)
        self._main_splitter.setChildrenCollapsible(False)
        self._main_splitter.addWidget(self._field_image_group)
        self._main_splitter.addWidget(self._map_group)
        self._main_splitter.addWidget(self._right_splitter)
        self._main_splitter.setStretchFactor(0, 2)
        self._main_splitter.setStretchFactor(1, 4)
        self._main_splitter.setStretchFactor(2, 2)
        self._main_splitter.setSizes([320, 640, 360])

        layout = QVBoxLayout()
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(8)
        layout.addLayout(controls_row)
        layout.addWidget(self._main_splitter, stretch=1)
        self.setLayout(layout)

        self._sync_cluster_controls()

    def set_display_options(self, options: DistanceMapDisplayOptions) -> None:
        normalized = options.normalized()
        self._display_options = normalized
        self._model_cluster_depth_checkbox.blockSignals(True)
        self._parallax_uncertainty_checkbox.blockSignals(True)
        self._catalog_sphere_checkbox.blockSignals(True)
        self._model_cluster_depth_checkbox.setChecked(normalized.model_cluster_depth)
        self._parallax_uncertainty_checkbox.setChecked(normalized.show_parallax_uncertainty)
        self._catalog_sphere_checkbox.setChecked(normalized.use_external_cluster_catalog)
        self._model_cluster_depth_checkbox.blockSignals(False)
        self._parallax_uncertainty_checkbox.blockSignals(False)
        self._catalog_sphere_checkbox.blockSignals(False)

    def display_options(self) -> DistanceMapDisplayOptions:
        return self._build_display_options().normalized()

    def _build_display_options(self) -> DistanceMapDisplayOptions:
        return DistanceMapDisplayOptions(
            model_cluster_depth=self._model_cluster_depth_checkbox.isChecked(),
            show_parallax_uncertainty=self._parallax_uncertainty_checkbox.isChecked(),
            use_external_cluster_catalog=self._catalog_sphere_checkbox.isChecked(),
        )

    def set_cluster_settings(self, settings: DistanceMapClusterSettings) -> None:
        self._cluster_settings = settings.normalized()
        self._sync_cluster_controls()

    def cluster_settings(self) -> DistanceMapClusterSettings:
        return self._cluster_settings.normalized()

    def set_source_image_path(self, source_path: Path | None) -> None:
        self._source_image_path = None if source_path is None else source_path.expanduser()

    def prepare_for_new_image(self, source_path: Path) -> None:
        self.set_source_image_path(source_path)
        self._current_result = None
        self._cluster_member_indices = frozenset()
        self._display_member_indices = frozenset()
        self._cluster_result = None
        self._results_table.setRowCount(0)
        self._map_view.clear()
        self._field_image_view.set_message("Building distance map...")
        self._only_cluster_checkbox.blockSignals(True)
        self._only_cluster_checkbox.setChecked(False)
        self._only_cluster_checkbox.blockSignals(False)
        self._sync_cluster_controls()
        self._disable_tomography(reset_button=True)
        self._sync_interactive_controls(is_busy=False)
        self._sync_apply_limits_enabled()
        self._refresh_field_image_view()

    def source_image_path(self) -> Path | None:
        if self._source_image_path is None:
            return None
        return self._source_image_path.expanduser()

    def max_magnitude(self) -> float:
        return float(self._max_magnitude_spin.value())

    def max_distance_pc(self) -> float:
        return float(self._max_distance_spin.value())

    def max_star_count(self) -> int:
        return int(self._max_stars_spin.value())

    def min_parallax_snr(self) -> float:
        return float(self._min_parallax_snr_spin.value())

    def set_limits(
        self,
        *,
        max_magnitude: float,
        max_distance_pc: float,
        max_star_count: int,
        min_parallax_snr: float,
    ) -> None:
        self._max_magnitude_spin.setValue(max_magnitude)
        self._max_distance_spin.setValue(max_distance_pc)
        self._max_stars_spin.setValue(max_star_count)
        self._min_parallax_snr_spin.setValue(min_parallax_snr)

    def set_busy(self, is_busy: bool) -> None:
        self._open_button.setEnabled(not is_busy)
        self._max_magnitude_spin.setEnabled(not is_busy)
        self._max_distance_spin.setEnabled(not is_busy)
        self._max_stars_spin.setEnabled(not is_busy)
        self._min_parallax_snr_spin.setEnabled(not is_busy)
        self._camera_mode_combo.setEnabled(not is_busy)
        self._show_labels_checkbox.setEnabled(not is_busy)
        self._distance_ruler_checkbox.setEnabled(not is_busy)
        self._reset_view_button.setEnabled(not is_busy)
        self._save_button.setEnabled(not is_busy)
        if is_busy:
            self._open_button.setText("Building...")
            self._apply_limits_button.setEnabled(False)
        else:
            self._open_button.setText("Open")
            self._sync_apply_limits_enabled()
        self._sync_interactive_controls(is_busy=is_busy)

    def _sync_apply_limits_enabled(self) -> None:
        has_source_image = self.source_image_path() is not None
        is_busy = self._open_button.text() == "Building..."
        self._apply_limits_button.setEnabled(has_source_image and not is_busy)

    def _sync_interactive_controls(self, *, is_busy: bool) -> None:
        has_stars = self._current_result is not None and bool(self._current_result.stars)
        interactive_enabled = (not is_busy) and has_stars
        self._find_cluster_button.setEnabled(interactive_enabled)
        self._tomography_button.setEnabled(interactive_enabled)
        self._distance_ruler_checkbox.setEnabled(interactive_enabled)
        self._model_cluster_depth_checkbox.setEnabled(interactive_enabled)
        self._parallax_uncertainty_checkbox.setEnabled(interactive_enabled)
        self._catalog_sphere_checkbox.setEnabled(interactive_enabled)

    def _handle_display_options_changed(self) -> None:
        self._display_options = self._build_display_options().normalized()
        self.display_options_changed.emit(self._display_options)
        result = self._current_result
        if result is not None:
            display_result = self._prepare_display_result(result)
            for note in display_result.notes:
                self.append_log(note)
        self._refresh_star_views()

    def append_log(self, message: str) -> None:
        if message:
            self._log_output.appendPlainText(message)

    def show_result(self, result: DistanceMapResult) -> None:
        self._current_result = result
        self._cluster_member_indices = frozenset()
        self._display_member_indices = frozenset()
        self._cluster_result = None
        self._only_cluster_checkbox.blockSignals(True)
        self._only_cluster_checkbox.setChecked(False)
        self._only_cluster_checkbox.blockSignals(False)
        self._refresh_star_views()
        self.append_log(result.report_text)
        self._sync_cluster_controls()
        self._sync_interactive_controls(is_busy=self._open_button.text() == "Building...")
        self._refresh_field_image_view()
        if self._tomography_button.isChecked():
            self._enable_tomography(show_missing_message=False)
        self._sync_apply_limits_enabled()

    def current_result(self) -> DistanceMapResult | None:
        return self._current_result

    def _handle_find_cluster_clicked(self) -> None:
        if self._cluster_member_indices:
            self._clear_cluster_selection()
            return
        self._find_cluster()

    def _find_cluster(self) -> None:
        result = self._current_result
        if result is None or not result.stars:
            QMessageBox.information(self, "No distance map", "Open an image first to build a distance map.")
            return

        cluster_result = find_distance_map_cluster(
            result.stars,
            strictness=self._cluster_settings.strictness,
            method=self._cluster_settings.method,
            parallax_mode=self._cluster_settings.parallax_mode,
            refine_magnitude_consistency=self._cluster_settings.refine_magnitude_consistency,
        )
        if cluster_result is None:
            QMessageBox.information(
                self,
                "No cluster found",
                "Could not identify a likely cluster from the current map stars. Try loosening the preset or increasing the star count.",
            )
            return

        self._cluster_result = cluster_result
        self._cluster_member_indices = frozenset(cluster_result.member_indices)
        if self._cluster_settings.auto_filter:
            self._only_cluster_checkbox.blockSignals(True)
            self._only_cluster_checkbox.setChecked(True)
            self._only_cluster_checkbox.blockSignals(False)
        self._refresh_star_views()

        method_text = distance_map_cluster_method_label(cluster_result.clustering_method)
        preset_text = distance_map_cluster_preset_label(self._cluster_settings.preset)
        refinement_text = " with magnitude cleanup" if cluster_result.used_magnitude_refinement else ""
        parallax_text = "parallax-aware" if cluster_result.used_parallax else "proper-motion-only"
        summary = (
            f"Highlighted {cluster_result.member_count} likely cluster member(s) "
            f"using the {preset_text} preset via {method_text} ({parallax_text}{refinement_text})."
        )
        self.append_log(summary)
        if self._display_options.model_cluster_depth:
            for note in self._prepare_display_result(result).notes:
                if note.startswith("Cluster members placed"):
                    self.append_log(note)
        self._sync_cluster_controls()

    def _clear_cluster_selection(self) -> None:
        self._cluster_member_indices = frozenset()
        self._display_member_indices = frozenset()
        self._cluster_result = None
        self._only_cluster_checkbox.blockSignals(True)
        self._only_cluster_checkbox.setChecked(False)
        self._only_cluster_checkbox.blockSignals(False)
        self._refresh_star_views()
        self.append_log("Returned to the full distance map selection.")
        self._sync_cluster_controls()

    def _open_cluster_dialog(self) -> None:
        dialog = DistanceMapClusterDialog(self._cluster_settings, self)
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return
        self._cluster_settings = dialog.build_settings()
        self.cluster_settings_changed.emit(self._cluster_settings)
        self._sync_cluster_controls()
        preset_text = distance_map_cluster_preset_label(self._cluster_settings.preset)
        self.append_log(f"Updated Distance Map cluster detection to the {preset_text} preset.")

    def _handle_camera_mode_changed(self) -> None:
        mode = str(self._camera_mode_combo.currentData() or "overview")
        self._map_view.set_camera_mode(mode)

    def _handle_show_labels_toggled(self, checked: bool) -> None:
        self._map_view.set_show_labels(checked)

    def _handle_distance_ruler_toggled(self, checked: bool) -> None:
        self._map_view.set_show_distance_ruler(checked)

    def _resolved_imaging_axes(self, result: DistanceMapResult) -> DistanceMapImagingAxes:
        try:
            wcs = WCS(read_header(result.solved_field.wcs_path))
            return distance_map_imaging_axes_from_field(result.solved_field, wcs)
        except Exception:
            return distance_map_imaging_axes(result.solved_field.center_ra_deg, result.solved_field.center_dec_deg)

    def _handle_reset_camera_view(self) -> None:
        self._map_view.reset_camera_view()

    def _default_save_path(self) -> Path:
        source_path = self.source_image_path()
        if source_path is not None:
            return source_path.with_name(f"{source_path.stem}_distance_map.png")
        return Path.home() / "distance_map.png"

    def _handle_save_view_image(self) -> None:
        if self._current_result is None or not self._current_result.stars:
            QMessageBox.information(self, "Nothing to save", "Open an image and build a distance map before saving.")
            return
        default_path = self._default_save_path()
        try:
            default_path.parent.mkdir(parents=True, exist_ok=True)
        except OSError:
            pass
        selected_path, _selected_filter = QFileDialog.getSaveFileName(
            self,
            "Save Distance Map Image",
            str(default_path),
            "PNG Files (*.png);;JPEG Files (*.jpg *.jpeg);;BMP Files (*.bmp);;All Files (*)",
            "PNG Files (*.png)",
        )
        if not selected_path:
            return
        output_path = Path(selected_path)
        if output_path.suffix.lower() not in {".png", ".jpg", ".jpeg", ".bmp"}:
            output_path = output_path.with_suffix(".png")
        image = self._map_view.capture_view_image()
        if image is None:
            QMessageBox.warning(self, "Save Distance Map Image", "Could not capture the 3D distance map view.")
            return
        try:
            output_path.parent.mkdir(parents=True, exist_ok=True)
        except OSError as exc:
            QMessageBox.warning(self, "Save Distance Map Image", str(exc))
            return
        if image.save(str(output_path)):
            self.append_log(f"Saved distance map image to {output_path}.")
            return
        QMessageBox.warning(self, "Save Distance Map Image", "Could not save the distance map image.")

    def _handle_tomography_toggled(self, enabled: bool) -> None:
        if enabled:
            if not self._enable_tomography(show_missing_message=True):
                self._tomography_button.blockSignals(True)
                self._tomography_button.setChecked(False)
                self._tomography_button.blockSignals(False)
            return
        self._disable_tomography(reset_button=False)

    def _enable_tomography(self, *, show_missing_message: bool) -> bool:
        result = self._current_result
        source_path = self.source_image_path()
        if result is None or not result.stars:
            if show_missing_message:
                QMessageBox.information(self, "No distance map", "Open an image and build a distance map before using Tomography.")
            return False
        if source_path is None or not source_path.exists():
            if show_missing_message:
                QMessageBox.information(self, "No field image", "Load a field image before using Tomography.")
            return False
        display = self._image_display_cache.get(str(source_path.resolve()))
        if display is None:
            try:
                display = build_annotated_image_display(source_path)
                self._image_display_cache[str(source_path.resolve())] = display
            except Exception as exc:
                if show_missing_message:
                    QMessageBox.warning(self, "Tomography unavailable", f"Could not load the field image for Tomography:\n{exc}")
                return False
        texture = _distance_map_tomography_texture(display)
        if texture is None:
            if show_missing_message:
                QMessageBox.warning(self, "Tomography unavailable", "Could not prepare the field image texture for Tomography.")
            return False
        imaging_axes = distance_map_imaging_axes(result.solved_field.center_ra_deg, result.solved_field.center_dec_deg)
        try:
            wcs = WCS(read_header(result.solved_field.wcs_path))
            imaging_axes = distance_map_imaging_axes_from_field(result.solved_field, wcs)
        except Exception:
            pass
        depth_min_pc, depth_max_pc = distance_map_tomography_depth_range(result.stars, imaging_axes.line_of_sight)
        depth_pc = distance_map_tomography_default_depth(result.stars, imaging_axes.line_of_sight)
        self._map_view.configure_tomography(
            texture=texture,
            imaging_axes=imaging_axes,
            solved_field=result.solved_field,
            depth_pc=depth_pc,
            depth_min_pc=depth_min_pc,
            depth_max_pc=depth_max_pc,
        )
        self._map_view.set_tomography_enabled(True)
        self._map_view.set_camera_mode("tomography-face")
        tomography_view_index = self._camera_mode_combo.findData("tomography-face")
        if tomography_view_index >= 0:
            self._camera_mode_combo.setCurrentIndex(tomography_view_index)
        self.append_log(
            f"Tomography enabled at {depth_pc:.1f} pc along the line of sight. "
            "Ctrl+left-drag in the 3D view to move the slice."
        )
        return True

    def _disable_tomography(self, *, reset_button: bool) -> None:
        was_enabled = self._map_view.tomography_enabled()
        self._map_view.clear_tomography()
        if was_enabled:
            self.append_log("Tomography disabled.")
        if reset_button:
            self._tomography_button.blockSignals(True)
            self._tomography_button.setChecked(False)
            self._tomography_button.blockSignals(False)

    def _handle_tomography_depth_changed(self, depth_pc: float) -> None:
        del depth_pc

    def _handle_display_filters_changed(self) -> None:
        self._refresh_star_views()

    def _sync_cluster_controls(self) -> None:
        has_manual_group = bool(self._cluster_member_indices)
        has_display_group = bool(self._display_member_indices)
        self._find_cluster_button.setText("Find All" if has_manual_group else "Find Cluster")
        self._only_cluster_checkbox.setEnabled(has_display_group)
        preset_text = distance_map_cluster_preset_label(self._cluster_settings.preset)
        if has_manual_group:
            self._find_cluster_button.setToolTip("Return to the full distance map selection.")
        else:
            self._find_cluster_button.setToolTip(
                f"Search for a likely moving cluster using the {preset_text} preset and highlight its members."
            )
        self._cluster_advanced_button.setToolTip(
            f"Adjust cluster detection presets. Current profile: {preset_text}."
        )

    def _refresh_field_image_view(self) -> None:
        result = self._current_result
        source_path = self.source_image_path()
        if source_path is None or not source_path.exists():
            self._field_image_view.set_message("Open an image to preview the field.")
            return

        try:
            cache_key = str(source_path.resolve())
            display = self._image_display_cache.get(cache_key)
            if display is None:
                display = build_annotated_image_display(source_path)
                self._image_display_cache[cache_key] = display
            overlays = self._cluster_image_overlays(result) if result is not None else []
            self._field_image_view.set_content(display, overlays=overlays, grid_overlays=[], editor_enabled=False, reset_view=False)
        except Exception as exc:
            self._field_image_view.set_message(f"Could not load the field image preview: {exc}")

    def _visible_star_indices(self, result: DistanceMapResult | None = None) -> list[int]:
        if result is None:
            result = self._current_result
        if result is None:
            return []
        if self._only_cluster_checkbox.isChecked() and self._display_member_indices:
            return [index for index in sorted(self._display_member_indices) if 0 <= index < len(result.stars)]
        return list(range(len(result.stars)))

    def _prepare_display_result(self, result: DistanceMapResult) -> DistanceMapDisplayResult:
        display_result = prepare_distance_map_display(
            result.stars,
            member_indices=self._cluster_member_indices,
            options=self._display_options,
            field_center_ra_deg=result.solved_field.center_ra_deg,
            field_center_dec_deg=result.solved_field.center_dec_deg,
            field_radius_deg=result.solved_field.radius_deg,
            cluster_settings=self._cluster_settings,
        )
        self._display_member_indices = display_result.effective_member_indices
        only_cluster = self._only_cluster_checkbox.isChecked() and bool(self._display_member_indices)
        if not only_cluster or display_result.uncertainty_segments is None:
            return display_result
        visible_indices = self._visible_star_indices(result)
        uncertainty_segments = build_parallax_uncertainty_segments(
            result.stars,
            member_indices=self._display_member_indices,
            visible_indices=visible_indices,
        )
        return replace(display_result, uncertainty_segments=uncertainty_segments)

    def _refresh_star_views(self) -> None:
        result = self._current_result
        if result is None:
            self._map_view.configure_imaging_axes(None)
            self._map_view.clear()
            self._results_table.setRowCount(0)
            self._refresh_field_image_view()
            return
        display_result = self._prepare_display_result(result)
        only_cluster = self._only_cluster_checkbox.isChecked() and bool(self._display_member_indices)
        self._map_view.configure_imaging_axes(self._resolved_imaging_axes(result))
        self._map_view.set_stars(
            display_result.display_stars,
            cluster_member_indices=self._display_member_indices,
            only_cluster=only_cluster,
            uncertainty_segments=display_result.uncertainty_segments,
            sphere_wireframe=display_result.sphere_wireframe,
        )
        visible_indices = self._visible_star_indices(result)
        visible_stars = tuple(display_result.display_stars[index] for index in visible_indices)
        self._populate_results_table(visible_stars, visible_indices=visible_indices)
        self._refresh_field_image_view()
        self._sync_cluster_controls()

    def _populate_results_table(self, stars: tuple[DistanceMapStar, ...], *, visible_indices: list[int]) -> None:
        self._results_table.setRowCount(len(stars))
        cluster_color = QColor("#ffe08a")
        for row_index, (star_index, star) in enumerate(zip(visible_indices, stars, strict=True)):
            magnitude_text = "-" if star.magnitude is None else f"{star.magnitude:.2f}"
            parallax_text = "-" if star.parallax_mas is None else f"{star.parallax_mas:.3f}"
            values = [
                star.name,
                magnitude_text,
                parallax_text,
                f"{star.distance_pc:.1f}",
                f"{star.ra_deg:.5f}, {star.dec_deg:.5f}",
            ]
            is_cluster_member = star_index in self._display_member_indices
            for column_index, text in enumerate(values):
                item = QTableWidgetItem(text)
                if is_cluster_member:
                    item.setBackground(cluster_color)
                self._results_table.setItem(row_index, column_index, item)
        self._results_table.resizeColumnsToContents()

    def _cluster_image_overlays(self, result: DistanceMapResult) -> list[ImageOverlay]:
        if not self._cluster_member_indices:
            return []
        try:
            wcs = WCS(read_header(result.solved_field.wcs_path))
        except Exception:
            return []

        overlays: list[ImageOverlay] = []
        show_all_labels = len(self._cluster_member_indices) <= 6
        for index in sorted(self._cluster_member_indices):
            if index < 0 or index >= len(result.stars):
                continue
            star = result.stars[index]
            pixel_position = distance_map_pixel_position(star, solved_field=result.solved_field, wcs=wcs)
            if pixel_position is None:
                continue
            pixel_x, pixel_y = pixel_position
            marker_radius = max(5.0, min(14.0, distance_map_star_point_size(star) * 0.65))
            overlays.append(
                ImageOverlay(
                    source_id=f"distance-map-cluster:{star.source_id}",
                    name=star.name,
                    x=pixel_x,
                    y=pixel_y,
                    aperture_radius=marker_radius,
                    annulus_inner_radius=marker_radius + 2.0,
                    annulus_outer_radius=marker_radius + 6.0,
                    color=distance_map_star_color_hex(star),
                    show_annulus=False,
                    show_handles=False,
                    show_label=show_all_labels,
                    pen_width=2.0,
                    show_center_dot=True,
                )
            )
        return overlays
