from __future__ import annotations



from dataclasses import dataclass

import hashlib

from pathlib import Path

from typing import Literal



from matplotlib.figure import Figure

import numpy as np

import pyqtgraph as pg

from PySide6.QtCore import QEvent, Qt, Signal

from PySide6.QtGui import QColor, QImage, QPainter

from PySide6.QtWidgets import QLabel, QVBoxLayout, QWidget



from photometry_app.core.hr_diagram import HrMeasurementRow, HrWorkingTable

from photometry_app.core.plotting import resolve_light_curve_theme_colors





_DEFAULT_APPARENT_MAG_MIN = -5.0

_DEFAULT_APPARENT_MAG_MAX = 30.0

_DEFAULT_AGE_GUIDE_GYR = 12.0

_DEFAULT_POINT_COLOR_SATURATION = 1.0

_DEFAULT_POINT_OPACITY = 0.8

_DEFAULT_X_LOG_SCALE = False

_DEFAULT_MARKER_SIZE_MODE = "scaled"

_DEFAULT_FIXED_MARKER_SIZE = 8.0

_DEFAULT_SHOW_CLASS_GUIDES = True

_SOLAR_ABSOLUTE_G_MAGNITUDE = 4.67

_TEMPERATURE_AXIS_LABEL = "Color Temperature (K)"

_LUMINOSITY_AXIS_LABEL = "Luminosity (L_sun)"

_BP_RP_COLOR_ANCHORS = np.asarray((-0.4, 0.0, 0.3, 0.6, 0.9, 1.2, 1.5, 1.8, 2.2, 2.6, 3.0), dtype=float)

_BP_RP_TEMPERATURE_ANCHORS = np.asarray((30000.0, 10000.0, 7600.0, 6200.0, 5300.0, 4600.0, 4000.0, 3600.0, 3200.0, 2900.0, 2600.0), dtype=float)

_TEMPERATURE_ASCENDING_ANCHORS = _BP_RP_TEMPERATURE_ANCHORS[::-1]

_COLOR_FOR_ASCENDING_TEMPERATURE = _BP_RP_COLOR_ANCHORS[::-1]





@dataclass(frozen=True)

class _HrClassGuideSpec:

    name: str

    color: str

    x_values: tuple[float, ...]

    y_values: tuple[float, ...]

    label_x: float

    label_y: float

    label_anchor: tuple[float, float] = (0.5, 0.5)





class _HrScaleAxisItem(pg.AxisItem):

    def __init__(self, orientation: str, parent: QWidget | None = None) -> None:

        super().__init__(orientation=orientation, parent=parent)

        self._display_mode: Literal[

            "identity",

            "temperature_from_color",

            "temperature_from_log_temperature",

            "color_from_log_temperature",

            "luminosity",

        ] = "identity"



    def set_display_mode(

        self,

        mode: Literal[

            "identity",

            "temperature_from_color",

            "temperature_from_log_temperature",

            "color_from_log_temperature",

            "luminosity",

        ],

    ) -> None:

        self._display_mode = mode

        self.update()



    def tickStrings(self, values: list[float], scale: float, spacing: float) -> list[str]:

        if self._display_mode == "temperature_from_color":

            return [HrDiagramPlotWidget.format_temperature_tick(HrDiagramPlotWidget.color_index_to_temperature_kelvin(value)) for value in values]

        if self._display_mode == "temperature_from_log_temperature":

            return [HrDiagramPlotWidget.format_temperature_tick(np.power(10.0, value)) for value in values]

        if self._display_mode == "color_from_log_temperature":

            return [HrDiagramPlotWidget.format_color_index_tick(HrDiagramPlotWidget.temperature_kelvin_to_color_index(np.power(10.0, value))) for value in values]

        if self._display_mode == "luminosity":

            return [HrDiagramPlotWidget.format_luminosity_tick(HrDiagramPlotWidget.absolute_magnitude_to_luminosity_ratio(value)) for value in values]

        return super().tickStrings(values, scale, spacing)



    def tickValues(self, minVal: float, maxVal: float, size: int) -> list[tuple[float, list[float]]]:

        if self._display_mode in {"temperature_from_log_temperature", "color_from_log_temperature"}:

            minimum_log = float(min(minVal, maxVal))

            maximum_log = float(max(minVal, maxVal))

            tick_positions = [

                float(np.log10(temperature))

                for temperature in _BP_RP_TEMPERATURE_ANCHORS

                if minimum_log - 1e-9 <= float(np.log10(temperature)) <= maximum_log + 1e-9

            ]

            if tick_positions:

                spacing = 1.0

                if len(tick_positions) > 1:

                    differences = np.diff(np.asarray(sorted(tick_positions), dtype=float))

                    finite_differences = differences[np.isfinite(differences)]

                    if finite_differences.size:

                        spacing = float(np.median(np.abs(finite_differences)))

                return [(max(spacing, 1e-6), sorted(tick_positions))]

        if self._display_mode == "luminosity":

            tick_positions = HrDiagramPlotWidget.luminosity_tick_positions_for_magnitude_range(minVal, maxVal)

            if tick_positions:

                spacing = 1.0

                if len(tick_positions) > 1:

                    differences = np.diff(np.asarray(tick_positions, dtype=float))

                    finite_differences = differences[np.isfinite(differences)]

                    if finite_differences.size:

                        spacing = float(np.median(np.abs(finite_differences)))

                return [(max(spacing, 1e-6), tick_positions)]

        return super().tickValues(minVal, maxVal, size)





class HrDiagramPlotWidget(QWidget):

    _MAX_DISPLAY_POINTS = 2000



    pointActivated = Signal(object)

    backgroundActivated = Signal()

    titleDoubleClicked = Signal()



    def __init__(self, parent: QWidget | None = None) -> None:

        super().__init__(parent)

        self._theme = "normal"

        self._theme_colors: dict[str, str] = {}

        self._bottom_axis = _HrScaleAxisItem(orientation="bottom")

        self._left_axis = _HrScaleAxisItem(orientation="left")

        self._top_axis = _HrScaleAxisItem(orientation="top")

        self._right_axis = pg.AxisItem(orientation="right")

        self._plot_item = pg.PlotItem(

            axisItems={

                "bottom": self._bottom_axis,

                "left": self._left_axis,

                "top": self._top_axis,

                "right": self._right_axis,

            }

        )

        self._plot_widget = pg.PlotWidget(background="w", plotItem=self._plot_item)

        self._plot_widget.scene().sigMouseClicked.connect(self._handle_scene_mouse_clicked)

        self._plot_widget.viewport().installEventFilter(self)

        self._plot_item.showGrid(x=True, y=True, alpha=0.25)

        self._plot_item.setMenuEnabled(False)

        self._plot_widget.setMinimumHeight(360)

        self._working_table: HrWorkingTable | None = None

        self._x_axis_mode = "gaia_bp_rp"

        self._y_axis_mode = "gaia_absolute_magnitude"

        self._plot_title = "HR Diagram"

        self._hide_flagged = False

        self._hide_saturated = True

        self._require_parallax = True

        self._apparent_magnitude_min = _DEFAULT_APPARENT_MAG_MIN

        self._apparent_magnitude_max = _DEFAULT_APPARENT_MAG_MAX

        self._show_age_guide = False

        self._age_guide_gyr = _DEFAULT_AGE_GUIDE_GYR

        self._show_class_guides = _DEFAULT_SHOW_CLASS_GUIDES

        self._point_color_saturation = _DEFAULT_POINT_COLOR_SATURATION

        self._point_opacity = _DEFAULT_POINT_OPACITY

        self._x_log_scale = _DEFAULT_X_LOG_SCALE

        self._selection_circle_color = "#ffd166"

        self._selection_circle_opacity = 0.85

        self._selection_circle_size_factor = 1.35

        self._marker_size_mode = _DEFAULT_MARKER_SIZE_MODE

        self._fixed_marker_size = _DEFAULT_FIXED_MARKER_SIZE

        self._table_row_limit = 1000

        self._visible_rows_cache: list[HrMeasurementRow] = []

        self._plotted_rows_cache: list[HrMeasurementRow] = []

        self._plotted_x_values_cache: list[float] = []

        self._plotted_y_values_cache: list[float] = []

        self._plotted_row_keys_cache: set[tuple[str, str]] = set()

        self._highlighted_row_keys_cache: set[tuple[str, str]] = set()

        self._plotted_point_sizes_cache: list[float] = []

        self._point_popup_item: pg.TextItem | None = None

        self._point_popup_row_key: tuple[str, str] | None = None

        self._point_popup_text: str | None = None

        self._export_rows_cache: list[HrMeasurementRow] = []

        self._export_x_values_cache: list[float] = []

        self._export_y_values_cache: list[float] = []

        self._class_guide_names_cache: tuple[str, ...] = ()

        self._age_guide_curve_cache: tuple[np.ndarray, np.ndarray] | None = None

        self._visible_row_count = 0

        self._plotted_row_count = 0

        self._row_mask: np.ndarray | None = None

        self._table_row_mask: np.ndarray | None = None

        self._selected_row: HrMeasurementRow | None = None

        self._auto_range_pending = True



        self._status_label = QLabel("Prepare an HR workflow to plot color index against Gaia absolute G magnitude.")

        self._status_label.setWordWrap(True)

        self._status_label.hide()



        layout = QVBoxLayout()

        layout.setContentsMargins(0, 0, 0, 0)

        layout.addWidget(self._plot_widget)

        layout.addWidget(self._status_label)

        self.setLayout(layout)

        self.set_theme("normal")

        self.show_message("Prepare an HR workflow to populate the diagram.")



    def visible_rows(self) -> list[HrMeasurementRow]:

        return list(self._visible_rows_cache)



    def visible_row_count(self) -> int:

        return int(self._visible_row_count)



    def plotted_row_count(self) -> int:

        return int(self._plotted_row_count)



    def plot_title(self) -> str:

        return str(self._plot_title)



    def set_plot_title(self, title: str) -> None:

        normalized_title = str(title)

        if self._plot_title == normalized_title:

            return

        self._plot_title = normalized_title

        self._plot_item.setTitle(self._plot_title, color=self._theme_colors["axis_color"])

    def eventFilter(self, watched: object, event: object) -> bool:

        if watched is self._plot_widget.viewport() and hasattr(event, "type") and event.type() == QEvent.Type.MouseButtonDblClick:

            title_label = getattr(self._plot_item, "titleLabel", None)

            if title_label is not None and hasattr(event, "position"):

                scene_position = self._plot_widget.mapToScene(event.position().toPoint())

                if title_label.sceneBoundingRect().contains(scene_position):

                    self.titleDoubleClicked.emit()

                    return True

        return super().eventFilter(watched, event)



    def current_view_ranges(self) -> tuple[tuple[float, float], tuple[float, float]] | None:

        view_range = self._plot_item.getViewBox().viewRange()

        if len(view_range) != 2 or len(view_range[0]) != 2 or len(view_range[1]) != 2:

            return None

        return (

            (float(view_range[0][0]), float(view_range[0][1])),

            (float(view_range[1][0]), float(view_range[1][1])),

        )



    def current_plot_aspect_ratio(self) -> float:

        size = self._plot_widget.size()

        width = max(1, int(size.width()))

        height = max(1, int(size.height()))

        return float(width) / float(height)



    def has_exportable_rows(self) -> bool:

        return bool(self._export_rows_cache)



    def scientific_export_rows(self) -> list[HrMeasurementRow]:

        return list(self._export_rows_cache)



    def export_current_view(self, output_path: str, scale_factor: float = 3.0) -> None:

        target_path = Path(output_path)

        size = self._plot_widget.size()

        source_width = max(1, int(size.width()))

        source_height = max(1, int(size.height()))

        resolved_scale = max(1.0, float(scale_factor))

        export_width = max(1, int(round(source_width * resolved_scale)))

        export_height = max(1, int(round(source_height * resolved_scale)))



        if target_path.suffix.lower() == ".svg":

            self._export_current_view_svg(str(target_path), source_width, source_height, export_width, export_height)

            return



        image = self._render_current_view_image(source_width, source_height, export_width, export_height)

        if target_path.suffix.lower() == ".pdf":

            self._export_current_view_pdf(str(target_path), image, export_width, export_height)

            return



        if not image.save(str(target_path)):

            raise OSError(f"Unable to save HR plot export to {target_path}")


    def capture_current_view_image(self, scale_factor: float = 1.0) -> QImage:

        size = self._plot_widget.size()

        source_width = max(1, int(size.width()))

        source_height = max(1, int(size.height()))

        resolved_scale = max(1.0, float(scale_factor))

        export_width = max(1, int(round(source_width * resolved_scale)))

        export_height = max(1, int(round(source_height * resolved_scale)))

        return self._render_current_view_image(source_width, source_height, export_width, export_height)



    def export_scientific_view(

        self,

        output_path: str,

        *,

        figure_size_inches: tuple[float, float] | None = None,

        dpi: int | None = None,

    ) -> None:

        if not self._export_rows_cache:

            raise OSError("No plottable HR rows are available for scientific export.")



        figure = Figure(figsize=(figure_size_inches or (8.5, 6.0)))

        try:

            axis = figure.add_subplot(111)

            axis.set_facecolor("#ffffff")

            figure.patch.set_facecolor("#ffffff")



            marker_sizes = [max(18.0, float(size) * float(size) * 1.6) for size in self._marker_sizes_for_rows(self._export_rows_cache)]

            marker_colors = [self._scientific_color_for_row(row) for row in self._export_rows_cache]

            axis.scatter(

                np.asarray(self._export_x_values_cache, dtype=float),

                np.asarray(self._export_y_values_cache, dtype=float),

                s=marker_sizes,

                c=marker_colors,

                linewidths=0.25,

                edgecolors="#1f1f1f",

                alpha=max(0.15, self._point_opacity),

            )

            highlighted_export_indices = [

                index

                for index, row in enumerate(self._export_rows_cache)

                if (row.catalog, row.source_id) in self._highlighted_row_keys_cache

            ]

            if highlighted_export_indices:

                axis.scatter(

                    np.asarray([self._export_x_values_cache[index] for index in highlighted_export_indices], dtype=float),

                    np.asarray([self._export_y_values_cache[index] for index in highlighted_export_indices], dtype=float),

                    s=np.asarray(

                        [self._selection_circle_export_size(marker_sizes[index]) for index in highlighted_export_indices],

                        dtype=float,

                    ),

                    facecolors="none",

                    edgecolors=self._selection_circle_color,

                    linewidths=1.6,

                    alpha=self._selection_circle_opacity,

                )



            if self._show_class_guides and self._class_guides_supported_for_axes():

                for guide in self._build_class_guide_specs():

                    curve_x, curve_y = self._interpolate_class_guide_curve(guide.x_values, guide.y_values, for_export=True)

                    axis.plot(curve_x, curve_y, linestyle="--", linewidth=1.8, color=guide.color, alpha=0.78)

                    label_x = self._export_x_value(float(guide.label_x))

                    if label_x is not None:

                        axis.text(label_x, guide.label_y, guide.name, color=guide.color, fontsize=9, alpha=0.92)



            if self._show_age_guide and self._age_guide_supported_for_axes():

                curve_x, curve_y = self._build_age_guide_curve(self._age_guide_gyr, for_export=True)

                finite_mask = np.isfinite(curve_x) & np.isfinite(curve_y)

                segment_start = 0

                while segment_start < len(curve_x):

                    while segment_start < len(curve_x) and not finite_mask[segment_start]:

                        segment_start += 1

                    segment_end = segment_start

                    while segment_end < len(curve_x) and finite_mask[segment_end]:

                        segment_end += 1

                    if segment_end > segment_start:

                        axis.plot(

                            curve_x[segment_start:segment_end],

                            curve_y[segment_start:segment_end],

                            linestyle="--",

                            linewidth=1.8,

                            color="#d97706",

                            alpha=0.9,

                        )

                    segment_start = segment_end + 1



            axis.set_title(self._plot_title)

            axis.grid(True, color="#d5d5d5", alpha=0.35, linewidth=0.8)

            for spine in axis.spines.values():

                spine.set_color("#444444")

            axis.tick_params(colors="#222222")



            primary_x_label = self._axis_label("x", self._x_axis_mode)

            primary_y_label = self._axis_label("y", self._y_axis_mode)

            if self._should_use_export_temperature_log_x_coordinates():

                axis.set_xlabel(_TEMPERATURE_AXIS_LABEL)

                axis.set_xscale("log")

                axis.tick_params(axis="x", labeltop=False, top=False, labelbottom=True, bottom=True, colors="#222222")

                tick_values = self._visible_temperature_tick_values()

                if tick_values:

                    axis.set_xticks(tick_values)

                    axis.set_xticklabels([self.format_temperature_tick(value) for value in tick_values])

                secondary_x_axis = axis.secondary_xaxis(

                    "top",

                    functions=(self.temperature_kelvin_to_color_index, self.color_index_to_temperature_kelvin),

                )

                secondary_x_axis.set_xlabel(primary_x_label)

                secondary_x_axis.tick_params(colors="#222222")

                secondary_x_axis.spines["top"].set_color("#444444")

                secondary_x_axis.xaxis.label.set_color("#222222")

                visible_color_ticks = self._visible_temperature_color_tick_values()

                if visible_color_ticks:

                    secondary_x_axis.set_xticks(visible_color_ticks)

                    secondary_x_axis.set_xticklabels([self.format_color_index_tick(value) for value in visible_color_ticks])

            elif self._should_use_x_log_scale():

                axis.set_xscale("log")

            if self._secondary_temperature_supported() and not self._should_use_export_temperature_log_x_coordinates():

                axis.set_xlabel(primary_x_label)

                axis.xaxis.set_label_position("top")

                axis.xaxis.tick_top()

                axis.tick_params(axis="x", labeltop=True, top=True, labelbottom=False, bottom=False, colors="#222222")

                secondary_x_axis = axis.secondary_xaxis(

                    "bottom",

                    functions=(self.color_index_to_temperature_kelvin, self.temperature_kelvin_to_color_index),

                )

                secondary_x_axis.set_xlabel(_TEMPERATURE_AXIS_LABEL)

                secondary_x_axis.tick_params(colors="#222222")

                secondary_x_axis.spines["bottom"].set_color("#444444")

                secondary_x_axis.xaxis.label.set_color("#222222")

                tick_values = self._visible_temperature_tick_values()

                if tick_values:

                    secondary_x_axis.set_xticks(tick_values)

                    secondary_x_axis.set_xticklabels([self.format_temperature_tick(value) for value in tick_values])

            else:

                axis.set_xlabel(primary_x_label)

                axis.tick_params(axis="x", labeltop=False, top=False, labelbottom=True, bottom=True, colors="#222222")



            if self._secondary_luminosity_supported():

                from matplotlib.ticker import FuncFormatter, LogLocator



                axis.set_ylabel(primary_y_label)

                axis.yaxis.set_label_position("right")

                axis.yaxis.tick_right()

                axis.tick_params(axis="y", labelright=True, right=True, labelleft=False, left=False, colors="#222222")

                secondary_y_axis = axis.secondary_yaxis(

                    "left",

                    functions=(self.absolute_magnitude_to_luminosity_ratio, self.luminosity_ratio_to_absolute_magnitude),

                )

                secondary_y_axis.set_ylabel(_LUMINOSITY_AXIS_LABEL)

                secondary_y_axis.tick_params(colors="#222222")

                secondary_y_axis.spines["left"].set_color("#444444")

                secondary_y_axis.yaxis.label.set_color("#222222")

                secondary_y_axis.yaxis.set_major_locator(LogLocator(base=10.0))

                secondary_y_axis.yaxis.set_major_formatter(FuncFormatter(lambda value, _pos: self.format_luminosity_tick(value)))

            else:

                axis.set_ylabel(primary_y_label)

                axis.tick_params(axis="y", labelright=False, right=False, labelleft=True, left=True, colors="#222222")



            view_ranges = self.current_view_ranges()

            if view_ranges is not None:

                x_limits, y_limits = view_ranges

                if self._should_use_export_temperature_log_x_coordinates():

                    axis.set_xlim(float(np.power(10.0, x_limits[1])), float(np.power(10.0, x_limits[0])))

                else:

                    axis.set_xlim(float(x_limits[0]), float(x_limits[1]))

                axis.set_ylim(float(y_limits[0]), float(y_limits[1]))

            elif self._should_use_export_temperature_log_x_coordinates():

                axis.invert_xaxis()

            axis.invert_yaxis()



            figure.tight_layout()

            figure.savefig(output_path, dpi=(dpi or 200), facecolor=figure.get_facecolor())

        finally:

            figure.clear()



    def plotted_row_keys(self) -> set[tuple[str, str]]:

        return set(self._plotted_row_keys_cache)



    def show_point_popup(self, row: HrMeasurementRow, text: str) -> None:

        self._point_popup_row_key = self._row_key(row)

        self._point_popup_text = str(text)

        self._refresh_point_popup_item()



    def hide_point_popup(self) -> None:

        self._point_popup_row_key = None

        self._point_popup_text = None

        self._remove_point_popup_item()



    def point_popup_text(self) -> str | None:

        return self._point_popup_text



    def _refresh_point_popup_item(self) -> None:

        self._remove_point_popup_item()

        if self._point_popup_row_key is None or self._point_popup_text is None:

            return

        popup_position = self._plotted_position_for_row_key(self._point_popup_row_key)

        if popup_position is None:

            return

        fill_color = QColor(self._theme_colors.get("background_color", "#ffffff"))

        fill_color.setAlpha(235)

        popup_item = pg.TextItem(

            text=self._point_popup_text,

            color=QColor(self._theme_colors.get("axis_color", "#222222")),

            anchor=(0.0, 1.0),

            border=pg.mkPen(self._theme_colors.get("grid_color", "#808080"), width=1),

            fill=pg.mkBrush(fill_color),

        )

        popup_item.setZValue(1_000_000)

        popup_item.setAcceptedMouseButtons(Qt.MouseButton.NoButton)

        popup_item.setPos(*popup_position)

        self._point_popup_item = popup_item

        self._plot_item.addItem(popup_item)



    def _remove_point_popup_item(self) -> None:

        popup_item = self._point_popup_item

        self._point_popup_item = None

        if popup_item is None:

            return

        try:

            self._plot_item.removeItem(popup_item)

        except Exception:

            pass



    def _plotted_position_for_row_key(self, row_key: tuple[str, str]) -> tuple[float, float] | None:

        normalized_row_key = (str(row_key[0]), str(row_key[1]))

        for row_index, plotted_row in enumerate(self._plotted_rows_cache):

            if self._row_key(plotted_row) != normalized_row_key:

                continue

            if row_index >= len(self._plotted_x_values_cache) or row_index >= len(self._plotted_y_values_cache):

                return None

            return (

                float(self._plotted_x_values_cache[row_index]),

                float(self._plotted_y_values_cache[row_index]),

            )

        return None



    @staticmethod

    def _row_key(row: HrMeasurementRow) -> tuple[str, str]:

        return (str(getattr(row, "catalog", "")), str(getattr(row, "source_id", "")))



    def selected_row(self) -> HrMeasurementRow | None:

        return self._selected_row



    def set_table_row_limit(self, row_limit: int) -> None:

        normalized_limit = max(1, int(row_limit))

        if self._table_row_limit == normalized_limit:

            return

        self._table_row_limit = normalized_limit

        if self._working_table is not None:

            self._rerender_working_table()



    def set_selected_row(self, row: HrMeasurementRow | None) -> None:

        if self._selected_row is row:

            return

        self._selected_row = row

        if self._working_table is not None:

            self._rerender_working_table()



    def set_theme(self, theme: str, custom_colors: dict[str, str] | None = None) -> None:

        normalized_theme = str(theme).strip().lower()

        self._theme = normalized_theme

        self._theme_colors = resolve_light_curve_theme_colors(normalized_theme, custom_colors)

        self._plot_widget.setBackground(self._theme_colors["background_color"])

        self._plot_item.showGrid(x=True, y=True, alpha=float(self._theme_colors["grid_alpha"]))

        self._plot_item.setTitle(self._plot_title, color=self._theme_colors["axis_color"])

        self._configure_plot_axes()

        self._refresh_point_popup_item()



    def show_message(self, message: str) -> None:

        self.hide_point_popup()

        self._working_table = None

        self._visible_rows_cache = []

        self._plotted_rows_cache = []

        self._plotted_x_values_cache = []

        self._plotted_y_values_cache = []

        self._plotted_row_keys_cache = set()

        self._plotted_point_sizes_cache = []

        self._export_rows_cache = []

        self._export_x_values_cache = []

        self._export_y_values_cache = []

        self._class_guide_names_cache = ()

        self._age_guide_curve_cache = None

        self._visible_row_count = 0

        self._plotted_row_count = 0

        self._row_mask = None

        self._table_row_mask = None

        self._selected_row = None

        self._auto_range_pending = True

        self._plot_item.clear()

        self._plot_item.setTitle(self._plot_title, color=self._theme_colors["axis_color"])

        text_item = pg.TextItem(message, anchor=(0.5, 0.5), color=self._theme_colors["empty_text_color"])

        self._plot_item.addItem(text_item)

        text_item.setPos(0.5, 0.5)

        self._configure_plot_axes()

        self._plot_item.getViewBox().invertY(True)

        self._plot_item.getViewBox().setRange(xRange=(0.0, 1.0), yRange=(0.0, 1.0), padding=0.0)

        self._status_label.setText(message)



    def set_axes(self, x_axis_mode: str, y_axis_mode: str) -> None:

        self._x_axis_mode = x_axis_mode

        self._y_axis_mode = y_axis_mode

        self._auto_range_pending = True

        self._rerender_working_table()



    def set_filters(self, *, hide_flagged: bool, hide_saturated: bool, require_parallax: bool) -> None:

        self._hide_flagged = hide_flagged

        self._hide_saturated = hide_saturated

        self._require_parallax = require_parallax

        self._rerender_working_table()



    def set_apparent_magnitude_filter(self, minimum_magnitude: float, maximum_magnitude: float) -> None:

        normalized_minimum, normalized_maximum = self._normalized_apparent_magnitude_range(

            minimum_magnitude,

            maximum_magnitude,

        )

        if (

            self._apparent_magnitude_min == normalized_minimum

            and self._apparent_magnitude_max == normalized_maximum

        ):

            return

        self._apparent_magnitude_min = normalized_minimum

        self._apparent_magnitude_max = normalized_maximum

        self._rerender_working_table()



    def plot_working_table(self, working_table: HrWorkingTable) -> None:

        if self._working_table is None:

            self._auto_range_pending = True

        self._working_table = working_table

        self._rerender_working_table()



    def apply_view(

        self,

        *,

        x_axis_mode: str,

        y_axis_mode: str,

        hide_flagged: bool,

        hide_saturated: bool,

        require_parallax: bool,

        apparent_magnitude_min: float,

        apparent_magnitude_max: float,

        show_age_guide: bool,

        age_guide_gyr: float,

        point_color_saturation: float,

        point_opacity: float,

        x_log_scale: bool = _DEFAULT_X_LOG_SCALE,

        selection_circle_color: str = "#ffd166",

        selection_circle_opacity: float = 0.85,

        selection_circle_size_factor: float = 1.35,

        marker_size_mode: str,

        fixed_marker_size: float,

        working_table: HrWorkingTable | None,

        show_class_guides: bool = _DEFAULT_SHOW_CLASS_GUIDES,

        row_mask: np.ndarray | None = None,

        table_row_mask: np.ndarray | None = None,

        highlighted_row_keys: set[tuple[str, str]] | None = None,

    ) -> None:

        previous_table = self._working_table

        previous_x_axis_mode = self._x_axis_mode

        previous_y_axis_mode = self._y_axis_mode

        previous_x_log_scale = self._x_log_scale

        self._x_axis_mode = x_axis_mode

        self._y_axis_mode = y_axis_mode

        self._hide_flagged = hide_flagged

        self._hide_saturated = hide_saturated

        self._require_parallax = require_parallax

        self._apparent_magnitude_min, self._apparent_magnitude_max = self._normalized_apparent_magnitude_range(

            apparent_magnitude_min,

            apparent_magnitude_max,

        )

        self._show_age_guide = bool(show_age_guide)

        self._age_guide_gyr = self._normalized_age_guide_gyr(age_guide_gyr)

        self._show_class_guides = bool(show_class_guides)

        self._point_color_saturation = self._normalized_point_color_saturation(point_color_saturation)

        self._point_opacity = self._normalized_point_opacity(point_opacity)

        self._x_log_scale = bool(x_log_scale)

        self._selection_circle_color = self._normalized_selection_circle_color(selection_circle_color)

        self._selection_circle_opacity = self._normalized_selection_circle_opacity(selection_circle_opacity)

        self._selection_circle_size_factor = self._normalized_selection_circle_size_factor(selection_circle_size_factor)

        self._marker_size_mode = self._normalized_marker_size_mode(marker_size_mode)

        self._fixed_marker_size = self._normalized_fixed_marker_size(fixed_marker_size)

        self._working_table = working_table

        self._row_mask = row_mask

        self._table_row_mask = table_row_mask

        self._highlighted_row_keys_cache = set() if highlighted_row_keys is None else {

            (str(catalog), str(source_id)) for catalog, source_id in highlighted_row_keys

        }

        if (

            previous_table is None

            or previous_x_axis_mode != x_axis_mode

            or previous_y_axis_mode != y_axis_mode

            or previous_x_log_scale != self._x_log_scale

        ):

            self._auto_range_pending = True

        if working_table is None:

            self.show_message("Prepare an HR workflow to populate the diagram.")

            return

        self._rerender_working_table()



    def reset_view(self) -> None:

        if self._working_table is None:

            self._plot_item.getViewBox().setRange(xRange=(0.0, 1.0), yRange=(0.0, 1.0), padding=0.0)

            return

        self._auto_range_pending = False

        self._plot_item.enableAutoRange()

        self._plot_item.getViewBox().autoRange()



    def _rerender_working_table(self) -> None:

        if self._working_table is None:

            self.hide_point_popup()

            self._visible_rows_cache = []

            self._plotted_rows_cache = []

            self._plotted_x_values_cache = []

            self._plotted_y_values_cache = []

            self._plotted_row_keys_cache = set()

            self._highlighted_row_keys_cache = set()

            self._plotted_point_sizes_cache = []

            self._export_rows_cache = []

            self._export_x_values_cache = []

            self._export_y_values_cache = []

            self._class_guide_names_cache = ()

            self._age_guide_curve_cache = None

            self._visible_row_count = 0

            self._plotted_row_count = 0

            return

        working_table = self._working_table

        had_visible_rows = bool(self._visible_rows_cache)

        self._point_popup_item = None

        self._plot_item.clear()

        self._plot_item.setTitle(self._plot_title, color=self._theme_colors["axis_color"])

        self._configure_plot_axes()

        self._plot_item.getViewBox().invertY(True)



        candidate_rows = working_table.rows

        plot_mask = self._row_mask if self._row_mask is not None and len(self._row_mask) == len(candidate_rows) else None

        table_mask = self._table_row_mask if self._table_row_mask is not None and len(self._table_row_mask) == len(candidate_rows) else None

        if table_mask is not None:

            candidate_indices = np.flatnonzero(table_mask).tolist()

        else:

            candidate_indices = list(range(len(candidate_rows)))



        table_rows: list[HrMeasurementRow] = []

        plottable_rows: list[HrMeasurementRow] = []

        plottable_x_values: list[float] = []

        export_x_values: list[float] = []

        plottable_y_values: list[float] = []

        display_rows: list[HrMeasurementRow] = []

        display_x_values: list[float] = []

        display_y_values: list[float] = []

        table_match_count = 0

        plottable_count = 0

        plotted_row_keys: set[tuple[str, str]] = set()

        selected_row = self._selected_row

        selected_key = None if selected_row is None else (selected_row.catalog, selected_row.source_id)

        selected_plottable_index = -1



        for candidate_index in candidate_indices:

            row = candidate_rows[candidate_index]

            if not self._row_matches_filters(row):

                continue

            x_value = self._row_axis_value(row, self._x_axis_mode)

            if x_value is None:

                continue

            y_value = self._row_axis_value(row, self._y_axis_mode)

            if y_value is None:

                continue

            plot_x_value = self._plot_x_value(x_value)

            export_x_value = self._export_x_value(x_value)

            if plot_x_value is None or export_x_value is None:

                continue

            row_key = (row.catalog, row.source_id)

            is_selected_row = selected_key is not None and row_key == selected_key

            table_match_count += 1

            if len(table_rows) < self._table_row_limit:

                table_rows.append(row)

            if plot_mask is not None and not bool(plot_mask[candidate_index]):

                continue

            plotted_row_keys.add(row_key)

            if is_selected_row:

                selected_plottable_index = len(plottable_rows)

            plottable_count += 1

            plottable_rows.append(row)

            plottable_x_values.append(plot_x_value)

            export_x_values.append(export_x_value)

            plottable_y_values.append(y_value)



        display_indices = self._stable_display_indices(

            plottable_rows,

            selected_index=selected_plottable_index,

        )

        selected_display_index = -1

        if display_indices:

            display_rows = [plottable_rows[index] for index in display_indices]

            display_x_values = [plottable_x_values[index] for index in display_indices]

            display_y_values = [plottable_y_values[index] for index in display_indices]

            if 0 <= selected_plottable_index < len(plottable_rows):

                try:

                    selected_display_index = display_indices.index(selected_plottable_index)

                except ValueError:

                    selected_display_index = -1



        self._visible_rows_cache = table_rows

        self._plotted_rows_cache = display_rows

        self._plotted_x_values_cache = list(display_x_values)

        self._plotted_y_values_cache = list(display_y_values)

        self._plotted_row_keys_cache = plotted_row_keys

        self._plotted_point_sizes_cache = self._marker_sizes_for_rows(display_rows)

        self._export_rows_cache = list(plottable_rows)

        self._export_x_values_cache = list(export_x_values)

        self._export_y_values_cache = list(plottable_y_values)

        self._visible_row_count = table_match_count

        self._plotted_row_count = plottable_count

        if not display_rows:

            self._show_inline_message("No plottable HR rows are available for the selected axes and filters.")

            return



        candidate_count = int(np.count_nonzero(plot_mask)) if plot_mask is not None else len(candidate_rows)

        status = f"Showing {len(display_rows)} of {plottable_count} plotted star(s), with {table_match_count} matching table row(s), from {len(working_table.rows)} total star(s)."



        self._add_class_guide_overlay()

        brushes = [self._brush_for_row(row) for row in display_rows]

        scatter = pg.ScatterPlotItem(

            x=display_x_values,

            y=display_y_values,

            data=list(range(len(display_rows))),

            size=self._plotted_point_sizes_cache,

            pen=self._point_pen(),

            brush=brushes,

        )

        scatter.sigClicked.connect(self._handle_scatter_clicked)

        self._plot_item.addItem(scatter)

        if 0 <= selected_display_index < len(display_rows):

            selected_size = self._plotted_point_sizes_cache[selected_display_index] if selected_display_index < len(self._plotted_point_sizes_cache) else 8.0

            highlight = pg.ScatterPlotItem(

                x=[display_x_values[selected_display_index]],

                y=[display_y_values[selected_display_index]],

                size=self._selection_circle_display_size(selected_size),

                pen=pg.mkPen(self._selection_circle_outline_color(), width=2.5),

                brush=pg.mkBrush(0, 0, 0, 0),

            )

            highlight.setAcceptedMouseButtons(Qt.MouseButton.NoButton)

            self._plot_item.addItem(highlight)

        common_motion_display_indices = [

            index

            for index, row in enumerate(display_rows)

            if index != selected_display_index and (row.catalog, row.source_id) in self._highlighted_row_keys_cache

        ]

        if common_motion_display_indices:

            common_motion_highlight = pg.ScatterPlotItem(

                x=[display_x_values[index] for index in common_motion_display_indices],

                y=[display_y_values[index] for index in common_motion_display_indices],

                size=[

                    self._selection_circle_display_size(self._plotted_point_sizes_cache[index])

                    for index in common_motion_display_indices

                ],

                pen=pg.mkPen(self._selection_circle_outline_color(), width=2.0),

                brush=pg.mkBrush(0, 0, 0, 0),

            )

            common_motion_highlight.setAcceptedMouseButtons(Qt.MouseButton.NoButton)

            self._plot_item.addItem(common_motion_highlight)

        self._add_age_guide_overlay()

        self._plot_item.setTitle(self._plot_title, color=self._theme_colors["axis_color"])

        if self._auto_range_pending or not had_visible_rows:

            self._plot_item.enableAutoRange()

            self._plot_item.getViewBox().autoRange()

            self._auto_range_pending = False

        if self._show_class_guides and not self._class_guides_supported_for_axes():

            status += " Class guides are available for Gaia BP-RP vs Gaia Absolute G Magnitude only."

        if self._show_age_guide and not self._age_guide_supported_for_axes():

            status += " Age guide is available for Gaia BP-RP vs Gaia Absolute G Magnitude only."

        elif self._age_guide_curve_cache is not None:

            status += f" Age guide: {self._age_guide_gyr:.1f} Gyr."

        self._refresh_point_popup_item()

        self._status_label.setText(status)



    def _stable_display_indices(

        self,

        rows: list[HrMeasurementRow],

        *,

        selected_index: int = -1,

    ) -> list[int]:

        if len(rows) <= self._MAX_DISPLAY_POINTS:

            return list(range(len(rows)))

        ranked_indices = sorted(range(len(rows)), key=lambda index: self._display_sample_sort_key(rows[index]))

        chosen_indices = ranked_indices[: self._MAX_DISPLAY_POINTS]

        if 0 <= selected_index < len(rows) and selected_index not in chosen_indices:

            chosen_indices[-1] = selected_index

        return sorted(set(chosen_indices))



    def _display_sample_sort_key(self, row: HrMeasurementRow) -> tuple[int, str, str]:

        row_key = f"{getattr(row, 'catalog', '')}|{getattr(row, 'source_id', '')}"

        digest = hashlib.blake2b(row_key.encode("utf-8", errors="ignore"), digest_size=8).digest()

        return (int.from_bytes(digest, byteorder="big", signed=False), str(getattr(row, "source_name", "")), row_key)



    def _show_inline_message(self, message: str) -> None:

        self._plotted_rows_cache = []

        self._plotted_x_values_cache = []

        self._plotted_y_values_cache = []

        self._plotted_row_keys_cache = set()

        self._highlighted_row_keys_cache = set()

        self._plotted_point_sizes_cache = []

        self._export_rows_cache = []

        self._export_x_values_cache = []

        self._export_y_values_cache = []

        self._class_guide_names_cache = ()

        self._age_guide_curve_cache = None

        self._plotted_row_count = 0

        self._plot_item.setTitle(self._plot_title, color=self._theme_colors["axis_color"])

        self.hide_point_popup()

        text_item = pg.TextItem(message, anchor=(0.5, 0.5), color=self._theme_colors["empty_text_color"])

        self._plot_item.addItem(text_item)

        text_item.setPos(0.5, 0.5)

        self._configure_plot_axes()

        self._plot_item.getViewBox().setRange(xRange=(0.0, 1.0), yRange=(0.0, 1.0), padding=0.0)

        self._status_label.setText(message)



    def _handle_scatter_clicked(self, _scatter: object, points: list[object], _event: object) -> None:

        if len(points) == 0:

            return

        accept = getattr(_event, "accept", None)

        if callable(accept):

            accept()

        point = points[0]

        try:

            point_index = int(point.data())

        except (TypeError, ValueError):

            return

        if 0 <= point_index < len(self._plotted_rows_cache):

            self.pointActivated.emit(self._plotted_rows_cache[point_index])



    def _handle_scene_mouse_clicked(self, event: object) -> None:

        if self._working_table is None or not self._plotted_rows_cache:

            return

        button_getter = getattr(event, "button", None)

        button = button_getter() if callable(button_getter) else button_getter

        if button != Qt.MouseButton.LeftButton:

            return

        accepted_getter = getattr(event, "isAccepted", None)

        if callable(accepted_getter) and bool(accepted_getter()):

            return

        scene_pos_getter = getattr(event, "scenePos", None)

        if not callable(scene_pos_getter):

            return

        scene_pos = scene_pos_getter()

        view_box = self._plot_item.getViewBox()

        if not view_box.sceneBoundingRect().contains(scene_pos):

            return

        self.backgroundActivated.emit()



    def _row_matches_filters(self, row: object) -> bool:

        if self._hide_saturated and getattr(row, "is_saturated", False):

            return False

        if self._hide_flagged and bool(getattr(row, "flags", [])):

            return False

        if self._require_parallax and getattr(row, "parallax_mas", None) is None:

            return False

        if (

            self._require_parallax

            and self._y_axis_mode in {"absolute_magnitude_proxy", "gaia_absolute_magnitude"}

            and getattr(row, self._y_axis_mode, None) is None

        ):

            return False

        if self._apparent_magnitude_filter_is_active():

            apparent_magnitude = self._row_axis_value(row, "gaia_g_mag")

            if apparent_magnitude is None:

                return False

            if apparent_magnitude < self._apparent_magnitude_min or apparent_magnitude > self._apparent_magnitude_max:

                return False

        return True



    def _row_axis_value(self, row: object, axis_mode: str) -> float | None:

        value = getattr(row, axis_mode, None)

        if value is None:

            return None

        try:

            numeric_value = float(value)

        except (TypeError, ValueError):

            return None

        return numeric_value if np.isfinite(numeric_value) else None



    def _axis_label(self, axis: str, axis_mode: str) -> str:

        labels = {

            "gaia_bp_rp": "Gaia BP-RP",

            "instrumental_blue_minus_red": "Instrumental Blue - Red",

            "plot_color_index": "Color Index",

            "absolute_magnitude_proxy": "Measured Absolute Magnitude Proxy",

            "gaia_absolute_magnitude": "Gaia Absolute G Magnitude",

            "calibrated_mag_luminance": "Calibrated Luminance Magnitude",

            "gaia_g_mag": "Gaia G Magnitude",

        }

        default_label = "Value" if axis == "x" else "Magnitude"

        return labels.get(axis_mode, default_label)



    def _configure_plot_axes(self) -> None:

        axis_pen = pg.mkPen(self._theme_colors["axis_color"])

        for axis_name in ("bottom", "left", "top", "right"):

            axis = self._plot_item.getAxis(axis_name)

            axis.setTextPen(axis_pen)

            axis.setPen(axis_pen)



        primary_x_label = self._axis_label("x", self._x_axis_mode)

        primary_y_label = self._axis_label("y", self._y_axis_mode)



        if self._secondary_temperature_supported():

            if self._should_use_plot_temperature_log_x_coordinates():

                self._bottom_axis.set_display_mode("temperature_from_log_temperature")

                self._top_axis.set_display_mode("color_from_log_temperature")

            else:

                self._bottom_axis.set_display_mode("temperature_from_color")

                self._top_axis.set_display_mode("identity")

            self._plot_item.showAxis("top")

            self._plot_item.setLabel("top", primary_x_label, color=self._theme_colors["axis_color"])

            self._plot_item.setLabel("bottom", _TEMPERATURE_AXIS_LABEL, color=self._theme_colors["axis_color"])

        else:

            self._bottom_axis.set_display_mode("identity")

            self._top_axis.set_display_mode("identity")

            self._plot_item.hideAxis("top")

            self._plot_item.setLabel("top", "", color=self._theme_colors["axis_color"])

            self._plot_item.setLabel("bottom", primary_x_label, color=self._theme_colors["axis_color"])



        if self._secondary_luminosity_supported():

            self._left_axis.set_display_mode("luminosity")

            self._plot_item.showAxis("right")

            self._plot_item.setLabel("right", primary_y_label, color=self._theme_colors["axis_color"])

            self._plot_item.setLabel("left", _LUMINOSITY_AXIS_LABEL, color=self._theme_colors["axis_color"])

        else:

            self._left_axis.set_display_mode("identity")

            self._plot_item.hideAxis("right")

            self._plot_item.setLabel("right", "", color=self._theme_colors["axis_color"])

            self._plot_item.setLabel("left", primary_y_label, color=self._theme_colors["axis_color"])



        self._plot_item.setLogMode(x=self._should_use_native_x_log_scale(), y=False)

        self._plot_item.getViewBox().invertX(self._should_use_plot_temperature_log_x_coordinates())



    def _secondary_temperature_supported(self) -> bool:

        return self._x_axis_mode == "gaia_bp_rp"



    def _secondary_luminosity_supported(self) -> bool:

        return self._y_axis_mode in {"gaia_absolute_magnitude", "absolute_magnitude_proxy"}



    @staticmethod

    def color_index_to_temperature_kelvin(values: float | np.ndarray) -> float | np.ndarray:

        numeric_values = np.asarray(values, dtype=float)

        temperatures = np.interp(

            numeric_values,

            _BP_RP_COLOR_ANCHORS,

            _BP_RP_TEMPERATURE_ANCHORS,

            left=float(_BP_RP_TEMPERATURE_ANCHORS[0]),

            right=float(_BP_RP_TEMPERATURE_ANCHORS[-1]),

        )

        if np.isscalar(values):

            return float(temperatures)

        return temperatures



    @staticmethod

    def temperature_kelvin_to_color_index(values: float | np.ndarray) -> float | np.ndarray:

        numeric_values = np.asarray(values, dtype=float)

        colors = np.interp(

            numeric_values,

            _TEMPERATURE_ASCENDING_ANCHORS,

            _COLOR_FOR_ASCENDING_TEMPERATURE,

            left=float(_COLOR_FOR_ASCENDING_TEMPERATURE[0]),

            right=float(_COLOR_FOR_ASCENDING_TEMPERATURE[-1]),

        )

        if np.isscalar(values):

            return float(colors)

        return colors



    @staticmethod

    def absolute_magnitude_to_luminosity_ratio(values: float | np.ndarray) -> float | np.ndarray:

        numeric_values = np.asarray(values, dtype=float)

        luminosity = np.power(10.0, (_SOLAR_ABSOLUTE_G_MAGNITUDE - numeric_values) / 2.5)

        if np.isscalar(values):

            return float(luminosity)

        return luminosity



    @staticmethod

    def luminosity_ratio_to_absolute_magnitude(values: float | np.ndarray) -> float | np.ndarray:

        numeric_values = np.asarray(values, dtype=float)

        clipped_values = np.clip(numeric_values, 1e-12, None)

        magnitudes = _SOLAR_ABSOLUTE_G_MAGNITUDE - (2.5 * np.log10(clipped_values))

        if np.isscalar(values):

            return float(magnitudes)

        return magnitudes



    @classmethod

    def luminosity_tick_positions_for_magnitude_range(cls, minimum_magnitude: float, maximum_magnitude: float) -> list[float]:

        lower_magnitude = float(min(minimum_magnitude, maximum_magnitude))

        upper_magnitude = float(max(minimum_magnitude, maximum_magnitude))

        minimum_luminosity = float(cls.absolute_magnitude_to_luminosity_ratio(upper_magnitude))

        maximum_luminosity = float(cls.absolute_magnitude_to_luminosity_ratio(lower_magnitude))

        if not np.isfinite(minimum_luminosity) or not np.isfinite(maximum_luminosity):

            return []

        minimum_luminosity = max(minimum_luminosity, 1e-12)

        maximum_luminosity = max(maximum_luminosity, minimum_luminosity)

        minimum_exponent = int(np.floor(np.log10(minimum_luminosity)))

        maximum_exponent = int(np.ceil(np.log10(maximum_luminosity)))

        tick_positions: list[float] = []

        for exponent in range(minimum_exponent, maximum_exponent + 1):

            magnitude = float(cls.luminosity_ratio_to_absolute_magnitude(10.0 ** exponent))

            if lower_magnitude - 1e-9 <= magnitude <= upper_magnitude + 1e-9:

                tick_positions.append(magnitude)

        return sorted(tick_positions)



    @staticmethod

    def format_temperature_tick(value: float) -> str:

        if not np.isfinite(value):

            return ""

        return f"{float(value):,.0f}"



    @staticmethod

    def format_color_index_tick(value: float | np.ndarray) -> str:

        try:

            numeric_value = float(np.asarray(value, dtype=float))

        except (TypeError, ValueError):

            return ""

        if not np.isfinite(numeric_value):

            return ""

        return f"{numeric_value:.2f}".rstrip("0").rstrip(".")



    @staticmethod

    def format_luminosity_tick(value: float) -> str:

        if not np.isfinite(value) or value <= 0.0:

            return ""

        exponent = int(round(np.log10(value)))

        if np.isclose(value, 10.0 ** exponent, rtol=1e-6, atol=1e-12):

            return f"1e{exponent}"

        if value >= 1000.0 or value < 0.1:

            return f"{float(value):.1e}"

        if value >= 100.0:

            return f"{float(value):,.0f}"

        if value >= 10.0:

            return f"{float(value):,.1f}"

        formatted = f"{float(value):,.2f}"

        return formatted.rstrip("0").rstrip(".")



    def _apparent_magnitude_filter_is_active(self) -> bool:

        return (

            abs(self._apparent_magnitude_min - _DEFAULT_APPARENT_MAG_MIN) > 1e-9

            or abs(self._apparent_magnitude_max - _DEFAULT_APPARENT_MAG_MAX) > 1e-9

        )



    def _normalized_apparent_magnitude_range(self, minimum_magnitude: float, maximum_magnitude: float) -> tuple[float, float]:

        normalized_minimum = min(_DEFAULT_APPARENT_MAG_MAX, max(_DEFAULT_APPARENT_MAG_MIN, float(minimum_magnitude)))

        normalized_maximum = min(_DEFAULT_APPARENT_MAG_MAX, max(_DEFAULT_APPARENT_MAG_MIN, float(maximum_magnitude)))

        return (min(normalized_minimum, normalized_maximum), max(normalized_minimum, normalized_maximum))



    def _normalized_point_color_saturation(self, value: float) -> float:

        return min(2.0, max(0.0, float(value)))



    def _normalized_point_opacity(self, value: float) -> float:

        return min(1.0, max(0.05, float(value)))



    def _should_use_x_log_scale(self) -> bool:

        if not self._x_log_scale or self._working_table is None:

            return False

        if self._secondary_temperature_supported():

            finite_temperature_values = [

                float(self.color_index_to_temperature_kelvin(value))

                for row in self._working_table.rows

                for value in [self._row_axis_value(row, self._x_axis_mode)]

                if value is not None and np.isfinite(value)

            ]

            return bool(finite_temperature_values) and min(finite_temperature_values) > 0.0

        finite_x_values = [

            float(value)

            for row in self._working_table.rows

            for value in [self._row_axis_value(row, self._x_axis_mode)]

            if value is not None and np.isfinite(value)

        ]

        return bool(finite_x_values) and min(finite_x_values) > 0.0



    def _should_use_native_x_log_scale(self) -> bool:

        return self._should_use_x_log_scale() and not self._should_use_plot_temperature_log_x_coordinates()



    def _should_use_plot_temperature_log_x_coordinates(self) -> bool:

        return self._secondary_temperature_supported() and self._should_use_x_log_scale()



    def _should_use_export_temperature_log_x_coordinates(self) -> bool:

        return self._secondary_temperature_supported() and self._should_use_x_log_scale()



    def _plot_x_value(self, raw_x_value: float) -> float | None:

        numeric_value = float(raw_x_value)

        if not np.isfinite(numeric_value):

            return None

        if not self._should_use_plot_temperature_log_x_coordinates():

            return numeric_value

        temperature = float(self.color_index_to_temperature_kelvin(numeric_value))

        if temperature <= 0.0 or not np.isfinite(temperature):

            return None

        return float(np.log10(temperature))



    def _export_x_value(self, raw_x_value: float) -> float | None:

        numeric_value = float(raw_x_value)

        if not np.isfinite(numeric_value):

            return None

        if not self._should_use_export_temperature_log_x_coordinates():

            return numeric_value

        temperature = float(self.color_index_to_temperature_kelvin(numeric_value))

        if temperature <= 0.0 or not np.isfinite(temperature):

            return None

        return temperature



    def _normalized_marker_size_mode(self, value: str) -> str:

        normalized = str(value or _DEFAULT_MARKER_SIZE_MODE).strip().lower()

        return normalized if normalized in {"scaled", "fixed"} else _DEFAULT_MARKER_SIZE_MODE



    def _normalized_fixed_marker_size(self, value: float) -> float:

        return min(24.0, max(2.0, float(value)))



    def _visible_temperature_tick_values(self) -> list[float]:

        if not self._secondary_temperature_supported():

            return []

        view_ranges = self.current_view_ranges()

        if self._should_use_plot_temperature_log_x_coordinates():

            if view_ranges is None:

                lower_temperature = float(np.min(_BP_RP_TEMPERATURE_ANCHORS))

                upper_temperature = float(np.max(_BP_RP_TEMPERATURE_ANCHORS))

            else:

                lower_temperature = float(np.power(10.0, min(view_ranges[0][0], view_ranges[0][1])))

                upper_temperature = float(np.power(10.0, max(view_ranges[0][0], view_ranges[0][1])))

            tick_values = [

                float(temperature)

                for temperature in _BP_RP_TEMPERATURE_ANCHORS

                if lower_temperature - 1e-9 <= float(temperature) <= upper_temperature + 1e-9

            ]

        else:

            if view_ranges is None:

                minimum_x = float(np.min(_BP_RP_COLOR_ANCHORS))

                maximum_x = float(np.max(_BP_RP_COLOR_ANCHORS))

            else:

                minimum_x = float(min(view_ranges[0][0], view_ranges[0][1]))

                maximum_x = float(max(view_ranges[0][0], view_ranges[0][1]))

            tick_values = [

                float(temperature)

                for temperature, color_index in zip(_BP_RP_TEMPERATURE_ANCHORS, _BP_RP_COLOR_ANCHORS)

                if minimum_x - 1e-9 <= float(color_index) <= maximum_x + 1e-9

            ]

        if not tick_values:

            tick_values = [float(np.max(_BP_RP_TEMPERATURE_ANCHORS)), float(np.min(_BP_RP_TEMPERATURE_ANCHORS))]

        return sorted({max(1.0, float(value)) for value in tick_values}, reverse=True)



    def _visible_temperature_color_tick_values(self) -> list[float]:

        return [float(self.temperature_kelvin_to_color_index(value)) for value in self._visible_temperature_tick_values()]



    def _normalized_age_guide_gyr(self, age_guide_gyr: float) -> float:

        return min(13.5, max(0.1, float(age_guide_gyr)))



    def _canonical_hr_axes_supported(self) -> bool:

        return self._x_axis_mode == "gaia_bp_rp" and self._y_axis_mode == "gaia_absolute_magnitude"



    def _class_guides_supported_for_axes(self) -> bool:

        return self._canonical_hr_axes_supported()



    def _age_guide_supported_for_axes(self) -> bool:

        return self._canonical_hr_axes_supported()



    def _add_class_guide_overlay(self) -> None:

        self._class_guide_names_cache = ()

        if not self._show_class_guides or not self._class_guides_supported_for_axes():

            return



        displayed_guides: list[str] = []

        for guide in self._build_class_guide_specs():

            curve_x, curve_y = self._interpolate_class_guide_curve(guide.x_values, guide.y_values)

            curve_item = pg.PlotDataItem(curve_x, curve_y, pen=self._class_guide_pen(guide.color), connect="finite")

            curve_item.setZValue(-25)

            self._plot_item.addItem(curve_item)

            label = pg.TextItem(guide.name, anchor=guide.label_anchor, color=self._class_guide_label_color(guide.color))

            label_x = self._plot_x_value(float(guide.label_x))

            if label_x is None:

                continue

            label.setPos(label_x, guide.label_y)

            label.setZValue(-20)

            self._plot_item.addItem(label)

            displayed_guides.append(guide.name)



        self._class_guide_names_cache = tuple(displayed_guides)



    def _build_class_guide_specs(self) -> tuple[_HrClassGuideSpec, ...]:

        return (

            _HrClassGuideSpec(

                name="Supergiants",

                color="#d62828",

                x_values=(-0.35, 0.15, 0.7, 1.35, 2.1, 2.85),

                y_values=(-9.4, -9.1, -8.7, -8.1, -7.0, -5.6),

                label_x=2.3,

                label_y=-7.4,

            ),

            _HrClassGuideSpec(

                name="Giants",

                color="#f77f00",

                x_values=(0.75, 1.1, 1.45, 1.9, 2.35, 2.8),

                y_values=(-2.7, -2.3, -1.8, -1.0, -0.1, 1.2),

                label_x=2.1,

                label_y=-0.6,

            ),

            _HrClassGuideSpec(

                name="Subgiants",

                color="#e9c46a",

                x_values=(0.4, 0.75, 1.1, 1.5, 1.95, 2.35),

                y_values=(0.2, 0.8, 1.6, 2.6, 3.7, 5.0),

                label_x=2.0,

                label_y=3.3,

            ),

            _HrClassGuideSpec(

                name="Main Sequence",

                color="#9aa0a6",

                x_values=(-0.4, -0.1, 0.25, 0.6, 0.95, 1.3, 1.7, 2.1, 2.55, 3.0),

                y_values=(-3.3, -1.8, 0.0, 2.2, 4.8, 7.0, 8.8, 10.3, 11.6, 13.0),

                label_x=1.2,

                label_y=5.9,

            ),

            _HrClassGuideSpec(

                name="White Dwarfs",

                color="#4cc9f0",

                x_values=(-0.55, -0.2, 0.15, 0.55, 0.95, 1.35),

                y_values=(8.1, 8.8, 9.8, 11.0, 12.3, 13.8),

                label_x=0.8,

                label_y=11.9,

            ),

        )



    def _interpolate_class_guide_curve(

        self,

        x_values: tuple[float, ...],

        y_values: tuple[float, ...],

        *,

        for_export: bool = False,

    ) -> tuple[np.ndarray, np.ndarray]:

        anchor_x = np.asarray(x_values, dtype=float)

        anchor_y = np.asarray(y_values, dtype=float)

        point_count = max(100, int((anchor_x.size - 1) * 24))

        curve_x = np.linspace(float(anchor_x[0]), float(anchor_x[-1]), point_count, dtype=float)

        curve_y = np.interp(curve_x, anchor_x, anchor_y)

        transform = self._export_x_value if for_export else self._plot_x_value

        transformed_x = np.asarray(

            [np.nan if not np.isfinite(value) else transform(float(value)) for value in curve_x],

            dtype=float,

        )

        finite_mask = np.isfinite(transformed_x) & np.isfinite(curve_y)

        transformed_x[~finite_mask] = np.nan

        curve_y[~finite_mask] = np.nan

        curve_x = transformed_x

        return curve_x, curve_y



    def _class_guide_pen(self, color_value: str) -> pg.QtGui.QPen:

        color = QColor(color_value)

        color.setAlphaF(0.7)

        return pg.mkPen(color, width=2.2, style=Qt.PenStyle.DashLine)



    def _class_guide_label_color(self, color_value: str) -> QColor:

        color = QColor(color_value)

        color.setAlphaF(0.9)

        return color



    def _add_age_guide_overlay(self) -> None:

        self._age_guide_curve_cache = None

        if not self._show_age_guide or not self._age_guide_supported_for_axes():

            return

        curve_x, curve_y = self._build_age_guide_curve(self._age_guide_gyr)

        self._age_guide_curve_cache = (curve_x, curve_y)

        guide_pen = pg.mkPen(self._theme_colors["fit_curve_color"], width=2.0, style=Qt.PenStyle.DashLine)

        curve_item = pg.PlotDataItem(curve_x, curve_y, pen=guide_pen, connect="finite")

        self._plot_item.addItem(curve_item)



    def _build_age_guide_curve(self, age_guide_gyr: float, *, for_export: bool = False) -> tuple[np.ndarray, np.ndarray]:

        age = self._normalized_age_guide_gyr(age_guide_gyr)

        normalized_age = (np.log10(age) - np.log10(0.1)) / (np.log10(13.5) - np.log10(0.1))

        normalized_age = float(min(1.0, max(0.0, normalized_age)))



        turnoff_color = 0.05 + (0.70 * normalized_age)

        turnoff_mag = -1.2 + (5.4 * normalized_age)

        giant_tip_color = min(1.65, turnoff_color + 0.82)

        horizontal_branch_mag = 0.9 - (0.25 * normalized_age)



        blue_main_x = np.linspace(-0.25, turnoff_color, 40, dtype=float)

        blue_offset = turnoff_color - blue_main_x

        blue_main_y = turnoff_mag - (3.6 * blue_offset) - (0.8 * np.square(blue_offset))



        red_main_x = np.linspace(turnoff_color, 1.95, 60, dtype=float)

        red_offset = red_main_x - turnoff_color

        red_main_y = turnoff_mag + (1.9 * red_offset) + (1.1 * np.square(red_offset))



        giant_x = np.linspace(turnoff_color, giant_tip_color, 40, dtype=float)

        giant_offset = giant_x - turnoff_color

        giant_y = (turnoff_mag - 0.35) - (5.8 * giant_offset) - (0.9 * np.square(giant_offset))



        horizontal_x = np.linspace(-0.05, min(0.65, turnoff_color + 0.08), 24, dtype=float)

        horizontal_y = horizontal_branch_mag + (0.18 * np.square(horizontal_x - np.mean(horizontal_x)))



        curve_x = np.concatenate((blue_main_x, red_main_x[1:], [np.nan], giant_x, [np.nan], horizontal_x))

        curve_y = np.concatenate((blue_main_y, red_main_y[1:], [np.nan], giant_y, [np.nan], horizontal_y))

        transform = self._export_x_value if for_export else self._plot_x_value

        transformed_x = np.asarray(

            [np.nan if not np.isfinite(value) else transform(float(value)) for value in curve_x],

            dtype=float,

        )

        finite_mask = np.isfinite(transformed_x) & np.isfinite(curve_y)

        transformed_x[~finite_mask] = np.nan

        curve_y[~finite_mask] = np.nan

        curve_x = transformed_x

        return curve_x, curve_y



    def _marker_sizes_for_rows(self, rows: list[HrMeasurementRow]) -> list[float]:

        if not rows:

            return []

        if self._marker_size_mode == "fixed":

            return [self._fixed_marker_size for _row in rows]



        finite_magnitudes = np.asarray(

            [float(row.gaia_g_mag) for row in rows if row.gaia_g_mag is not None and np.isfinite(float(row.gaia_g_mag))],

            dtype=float,

        )

        if finite_magnitudes.size == 0:

            return [8.0 for _row in rows]



        bright_reference = float(np.nanpercentile(finite_magnitudes, 5.0))

        faint_reference = float(np.nanpercentile(finite_magnitudes, 95.0))

        if not np.isfinite(bright_reference) or not np.isfinite(faint_reference) or faint_reference <= bright_reference:

            return [9.0 for _row in rows]



        minimum_size = 5.0

        maximum_size = 14.0

        marker_sizes: list[float] = []

        for row in rows:

            if row.gaia_g_mag is None:

                marker_sizes.append(8.0)

                continue

            try:

                magnitude = float(row.gaia_g_mag)

            except (TypeError, ValueError):

                marker_sizes.append(8.0)

                continue

            if not np.isfinite(magnitude):

                marker_sizes.append(8.0)

                continue

            normalized_brightness = (faint_reference - magnitude) / (faint_reference - bright_reference)

            normalized_brightness = min(1.0, max(0.0, normalized_brightness))

            marker_sizes.append(minimum_size + ((maximum_size - minimum_size) * normalized_brightness))

        return marker_sizes



    def _brush_for_row(self, row: HrMeasurementRow) -> pg.QtGui.QBrush:

        color = self._plot_color(row.display_color_hex or self._theme_colors["point_brush"], opacity=self._point_opacity)

        return pg.mkBrush(color)



    def _point_pen(self) -> pg.QtGui.QPen:

        color = self._plot_color(self._theme_colors["point_pen"], opacity=min(1.0, max(0.2, self._point_opacity)))

        return pg.mkPen(color, width=1.0)



    def _plot_color(self, color_value: str, *, opacity: float) -> QColor:

        color = QColor(color_value)

        if not color.isValid():

            color = QColor(self._theme_colors["point_brush"])

        hue, saturation, value, _alpha = color.getHsvF()

        if hue >= 0.0 and saturation >= 0.0 and value >= 0.0:

            return QColor.fromHsvF(

                hue,

                min(1.0, max(0.0, saturation * self._point_color_saturation)),

                value,

                self._normalized_point_opacity(opacity),

            )

        adjusted = QColor(color)

        adjusted.setAlphaF(self._normalized_point_opacity(opacity))

        return adjusted



    def _scientific_color_for_row(self, row: HrMeasurementRow) -> tuple[float, float, float, float]:

        color = self._plot_color(row.display_color_hex or self._theme_colors["point_brush"], opacity=max(0.25, self._point_opacity))

        return (color.redF(), color.greenF(), color.blueF(), color.alphaF())



    def _render_current_view_image(

        self,

        source_width: int,

        source_height: int,

        export_width: int,

        export_height: int,

    ) -> QImage:

        snapshot = self._plot_widget.grab().toImage()

        if snapshot.isNull():

            raise OSError("Unable to capture the current HR plot view")

        image = QImage(snapshot)

        image.setDevicePixelRatio(1.0)

        if image.width() == export_width and image.height() == export_height:

            return image

        image = image.scaled(

            export_width,

            export_height,

            Qt.AspectRatioMode.IgnoreAspectRatio,

            Qt.TransformationMode.SmoothTransformation,

        )

        image.setDevicePixelRatio(1.0)

        return image



    def _export_current_view_pdf(self, output_path: str, image: QImage, export_width: int, export_height: int) -> None:

        from PySide6.QtCore import QRectF, QSizeF

        from PySide6.QtGui import QPageSize, QPdfWriter



        writer = QPdfWriter(output_path)

        writer.setResolution(72)

        writer.setPageSize(QPageSize(QSizeF(float(export_width), float(export_height)), QPageSize.Unit.Point))

        painter = QPainter(writer)

        try:

            painter.drawImage(QRectF(0.0, 0.0, float(export_width), float(export_height)), image)

        finally:

            painter.end()



    def _export_current_view_svg(

        self,

        output_path: str,

        source_width: int,

        source_height: int,

        export_width: int,

        export_height: int,

    ) -> None:

        from PySide6.QtCore import QRect, QSize

        from PySide6.QtSvg import QSvgGenerator



        generator = QSvgGenerator()

        generator.setFileName(output_path)

        generator.setSize(QSize(export_width, export_height))

        generator.setViewBox(QRect(0, 0, export_width, export_height))

        generator.setTitle(self._plot_title or "Citizen Photometry HR Diagram")



    def _normalized_selection_circle_color(self, color: str) -> str:

        candidate = QColor(str(color).strip())

        return candidate.name(QColor.NameFormat.HexRgb).lower() if candidate.isValid() else "#ffd166"



    def _normalized_selection_circle_opacity(self, opacity: float) -> float:

        return min(1.0, max(0.0, float(opacity)))



    def _normalized_selection_circle_size_factor(self, factor: float) -> float:

        return min(4.0, max(1.0, float(factor)))



    def _selection_circle_outline_color(self) -> QColor:

        outline_color = QColor(self._selection_circle_color)

        if not outline_color.isValid():

            outline_color = QColor("#ffd166")

        outline_color.setAlphaF(self._selection_circle_opacity)

        return outline_color



    def _selection_circle_display_size(self, point_size: float) -> float:

        normalized_point_size = max(0.0, float(point_size))

        return max(normalized_point_size, normalized_point_size * self._selection_circle_size_factor)



    def _selection_circle_export_size(self, marker_area: float) -> float:

        normalized_marker_area = max(0.0, float(marker_area))

        return max(normalized_marker_area, normalized_marker_area * (self._selection_circle_size_factor ** 2))



        painter = QPainter(generator)

        try:

            painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)

            painter.setRenderHint(QPainter.RenderHint.TextAntialiasing, True)

            painter.scale(export_width / source_width, export_height / source_height)

            self._plot_widget.render(painter)

        finally:

            painter.end()