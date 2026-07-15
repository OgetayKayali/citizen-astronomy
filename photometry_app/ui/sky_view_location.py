from __future__ import annotations

from concurrent.futures import Future, ThreadPoolExecutor
from dataclasses import dataclass
import math
import os

import requests

from PySide6.QtCore import QPointF, QRectF, Qt, QTimer, Signal
from PySide6.QtGui import QColor, QCloseEvent, QImage, QMouseEvent, QPainter, QPainterPath, QPaintEvent, QPen, QWheelEvent
from PySide6.QtWidgets import (
    QAbstractSpinBox,
    QCheckBox,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QDoubleSpinBox,
    QFormLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from photometry_app.core.settings import AppSettings, ObservingSitePreset


_REVERSE_GEOCODE_URL = "https://nominatim.openstreetmap.org/reverse"
_REVERSE_GEOCODE_HEADERS = {
    "Accept": "application/json",
    "User-Agent": "CitizenAstronomy/0.1 (Sky View observer location)",
}
_REVERSE_GEOCODE_CACHE: dict[tuple[float, float], str] = {}
_MAP_TILE_SIZE_PX = 256
_MAP_TILE_MIN_ZOOM = 2
_MAP_TILE_MAX_ZOOM = 18
_MAP_TILE_MAX_MERCATOR_LATITUDE_DEG = 85.05112878
_MAP_TILE_HEADERS = {
    "User-Agent": "CitizenAstronomy/0.1 (Sky View observer location map)",
}
_MAP_TILE_EXECUTOR = ThreadPoolExecutor(max_workers=4, thread_name_prefix="sky-view-map-tile")


def _map_tile_url(zoom_level: int, tile_x: int, tile_y: int) -> str:

    subdomain = "abcd"[(int(tile_x) + int(tile_y)) % 4]

    return f"https://{subdomain}.basemaps.cartocdn.com/dark_all/{int(zoom_level)}/{int(tile_x)}/{int(tile_y)}.png"


def _download_map_tile(zoom_level: int, tile_x: int, tile_y: int) -> bytes | None:

    try:
        response = requests.get(
            _map_tile_url(zoom_level, tile_x, tile_y),
            headers=_MAP_TILE_HEADERS,
            timeout=12,
        )
        response.raise_for_status()
        return response.content
    except Exception:
        return None


@dataclass(frozen=True)
class _SkyViewObservingSiteSelection:

    site_name: str

    latitude_deg: float

    longitude_deg: float

    elevation_m: float | None


def _normalize_latitude(latitude_deg: float) -> float:

    return max(-90.0, min(90.0, float(latitude_deg)))


def _normalize_longitude(longitude_deg: float) -> float:

    return ((float(longitude_deg) + 180.0) % 360.0) - 180.0


def _observing_site_display_name(name: str, latitude_deg: float, longitude_deg: float) -> str:

    normalized_name = str(name or "").strip()

    if normalized_name:

        return normalized_name

    latitude_suffix = "N" if latitude_deg >= 0.0 else "S"

    longitude_suffix = "E" if longitude_deg >= 0.0 else "W"

    return f"{abs(float(latitude_deg)):.3f} deg {latitude_suffix}, {abs(float(longitude_deg)):.3f} deg {longitude_suffix}"


def _reverse_geocode_observing_site(latitude_deg: float, longitude_deg: float) -> str | None:

    cache_key = (round(float(latitude_deg), 4), round(float(longitude_deg), 4))

    cached_name = _REVERSE_GEOCODE_CACHE.get(cache_key)

    if cached_name:

        return cached_name

    try:

        response = requests.get(
            _REVERSE_GEOCODE_URL,
            params={
                "format": "jsonv2",
                "lat": f"{float(latitude_deg):.6f}",
                "lon": f"{float(longitude_deg):.6f}",
                "zoom": "10",
            },
            headers=_REVERSE_GEOCODE_HEADERS,
            timeout=12,
        )
        response.raise_for_status()
        payload = response.json()
    except Exception:
        return None

    address = payload.get("address") if isinstance(payload, dict) else None
    if isinstance(address, dict):
        candidates = [
            address.get("city"),
            address.get("town"),
            address.get("village"),
            address.get("municipality"),
            address.get("county"),
            address.get("state"),
            address.get("country"),
        ]
        parts: list[str] = []
        for value in candidates:
            normalized = str(value or "").strip()
            if not normalized or normalized in parts:
                continue
            parts.append(normalized)
        if parts:
            resolved_name = ", ".join(parts[:3])
            _REVERSE_GEOCODE_CACHE[cache_key] = resolved_name
            return resolved_name

    display_name = str(payload.get("display_name", "") if isinstance(payload, dict) else "").strip()
    if not display_name:
        return None

    resolved_name = ", ".join(segment.strip() for segment in display_name.split(",")[:3] if segment.strip())
    if resolved_name:
        _REVERSE_GEOCODE_CACHE[cache_key] = resolved_name
        return resolved_name
    return None


class _SkyViewLocationMapWidget(QWidget):

    selectionChanged = Signal(float, float)

    def __init__(self, latitude_deg: float = 0.0, longitude_deg: float = 0.0, parent: QWidget | None = None) -> None:

        super().__init__(parent)

        self._selected_latitude_deg = _normalize_latitude(latitude_deg)

        self._selected_longitude_deg = _normalize_longitude(longitude_deg)

        self._default_zoom_level = 11.0 if abs(self._selected_latitude_deg) > 0.01 or abs(self._selected_longitude_deg) > 0.01 else 2.25

        self._current_zoom_level = int(round(self._default_zoom_level))

        self._center_world_x = 0.0

        self._center_world_y = 0.0

        self._drag_start_position: QPointF | None = None

        self._drag_start_center: tuple[float, float] | None = None

        self._drag_moved = False

        self._tile_images: dict[tuple[int, int, int], QImage] = {}

        self._tile_futures: dict[tuple[int, int, int], Future[bytes | None]] = {}

        self._tile_poll_timer = QTimer(self)
        self._tile_poll_timer.setInterval(80)
        self._tile_poll_timer.timeout.connect(self._check_tile_futures)

        self.setMinimumSize(520, 340)
        self.setMouseTracking(True)
        self.setCursor(Qt.CursorShape.OpenHandCursor)
        self.setAutoFillBackground(False)
        self._set_center_from_location(self._selected_latitude_deg, self._selected_longitude_deg)

        self._request_visible_tiles()

    def selected_location(self) -> tuple[float, float]:

        return self._selected_latitude_deg, self._selected_longitude_deg

    def set_selected_location(self, latitude_deg: float, longitude_deg: float, *, recenter: bool) -> None:

        normalized_latitude = _normalize_latitude(latitude_deg)

        normalized_longitude = _normalize_longitude(longitude_deg)

        changed = (
            abs(normalized_latitude - self._selected_latitude_deg) > 1.0e-6
            or abs(normalized_longitude - self._selected_longitude_deg) > 1.0e-6
        )

        self._selected_latitude_deg = normalized_latitude

        self._selected_longitude_deg = normalized_longitude

        if recenter:
            self._set_center_from_location(self._selected_latitude_deg, self._selected_longitude_deg)

        self._request_visible_tiles()
        self.update()

        if changed:
            self.selectionChanged.emit(self._selected_latitude_deg, self._selected_longitude_deg)

    def zoom_in(self) -> None:

        self._set_zoom_level(self._current_zoom_level + 1)

    def zoom_out(self) -> None:

        self._set_zoom_level(self._current_zoom_level - 1)

    def reset_view(self) -> None:

        self._current_zoom_level = int(round(self._default_zoom_level))
        self._set_center_from_location(self._selected_latitude_deg, self._selected_longitude_deg)
        self._request_visible_tiles()
        self.update()

    def paintEvent(self, event: QPaintEvent) -> None:

        super().paintEvent(event)

        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        bounds = QRectF(self.rect())
        clip_path = QPainterPath()
        clip_path.addRoundedRect(bounds, 18.0, 18.0)
        painter.setClipPath(clip_path)
        painter.fillRect(bounds, QColor("#020713"))
        self._draw_tiles(painter, bounds)
        self._draw_map_overlays(painter, bounds)
        self._draw_selection_marker(painter, bounds)

    def resizeEvent(self, event) -> None:  # type: ignore[override]

        super().resizeEvent(event)
        self._request_visible_tiles()

    def mousePressEvent(self, event: QMouseEvent) -> None:

        if event.button() == Qt.MouseButton.LeftButton:
            self._drag_start_position = event.position()
            self._drag_start_center = (self._center_world_x, self._center_world_y)
            self._drag_moved = False
            self.setCursor(Qt.CursorShape.ClosedHandCursor)
            event.accept()
            return
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event: QMouseEvent) -> None:

        if self._drag_start_position is not None and self._drag_start_center is not None:
            delta = event.position() - self._drag_start_position
            if abs(delta.x()) > 3.0 or abs(delta.y()) > 3.0:
                self._drag_moved = True
            self._center_world_x = self._wrap_world_x(self._drag_start_center[0] - float(delta.x()))
            self._center_world_y = self._clamp_world_y(self._drag_start_center[1] - float(delta.y()))
            self._request_visible_tiles()
            self.update()
            event.accept()
            return
        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event: QMouseEvent) -> None:

        if event.button() == Qt.MouseButton.LeftButton and self._drag_start_position is not None:
            click_position = event.position()
            drag_moved = self._drag_moved
            self._drag_start_position = None
            self._drag_start_center = None
            self._drag_moved = False
            self.setCursor(Qt.CursorShape.OpenHandCursor)
            if not drag_moved:
                latitude_deg, longitude_deg = self._screen_point_to_location(click_position)
                self.set_selected_location(latitude_deg, longitude_deg, recenter=False)
            event.accept()
            return
        super().mouseReleaseEvent(event)

    def wheelEvent(self, event: QWheelEvent) -> None:

        delta = event.angleDelta().y()
        if delta == 0:
            super().wheelEvent(event)
            return
        step = 1 if delta > 0 else -1
        self._set_zoom_level(self._current_zoom_level + step, anchor_point=event.position())
        event.accept()

    def _set_zoom_level(self, zoom_level: int, anchor_point: QPointF | None = None) -> None:

        old_zoom = self._current_zoom_level
        new_zoom = max(_MAP_TILE_MIN_ZOOM, min(_MAP_TILE_MAX_ZOOM, int(zoom_level)))
        if new_zoom == old_zoom:
            return
        if anchor_point is None:
            center_latitude, center_longitude = self._world_pixel_to_location(self._center_world_x, self._center_world_y, old_zoom)
            self._current_zoom_level = new_zoom
            self._set_center_from_location(center_latitude, center_longitude)
        else:
            anchor_latitude, anchor_longitude = self._screen_point_to_location(anchor_point)
            self._current_zoom_level = new_zoom
            anchor_world_x, anchor_world_y = self._location_to_world_pixel(anchor_latitude, anchor_longitude, new_zoom)
            self._center_world_x = self._wrap_world_x(anchor_world_x - (float(anchor_point.x()) - (self.width() / 2.0)))
            self._center_world_y = self._clamp_world_y(anchor_world_y - (float(anchor_point.y()) - (self.height() / 2.0)))
        self._request_visible_tiles()
        self.update()

    def _set_center_from_location(self, latitude_deg: float, longitude_deg: float) -> None:

        self._center_world_x, self._center_world_y = self._location_to_world_pixel(latitude_deg, longitude_deg, self._current_zoom_level)
        self._center_world_y = self._clamp_world_y(self._center_world_y)

    def _location_to_world_pixel(self, latitude_deg: float, longitude_deg: float, zoom_level: int) -> tuple[float, float]:

        latitude = max(-_MAP_TILE_MAX_MERCATOR_LATITUDE_DEG, min(_MAP_TILE_MAX_MERCATOR_LATITUDE_DEG, float(latitude_deg)))
        longitude = _normalize_longitude(longitude_deg)
        world_size = self._world_size(zoom_level)
        sin_latitude = math.sin(math.radians(latitude))
        pixel_x = ((longitude + 180.0) / 360.0) * world_size
        pixel_y = (0.5 - (math.log((1.0 + sin_latitude) / (1.0 - sin_latitude)) / (4.0 * math.pi))) * world_size
        return self._wrap_world_x(pixel_x, zoom_level=zoom_level), self._clamp_world_y(pixel_y, zoom_level=zoom_level)

    def _world_pixel_to_location(self, pixel_x: float, pixel_y: float, zoom_level: int) -> tuple[float, float]:

        world_size = self._world_size(zoom_level)
        wrapped_x = float(pixel_x) % world_size
        clamped_y = max(0.0, min(world_size, float(pixel_y)))
        longitude_deg = (wrapped_x / world_size) * 360.0 - 180.0
        mercator_n = math.pi - (2.0 * math.pi * clamped_y / world_size)
        latitude_deg = math.degrees(math.atan(math.sinh(mercator_n)))
        return _normalize_latitude(latitude_deg), _normalize_longitude(longitude_deg)

    def _screen_point_to_location(self, point: QPointF) -> tuple[float, float]:

        world_x = self._center_world_x + (float(point.x()) - (self.width() / 2.0))
        world_y = self._center_world_y + (float(point.y()) - (self.height() / 2.0))
        return self._world_pixel_to_location(world_x, world_y, self._current_zoom_level)

    def _location_to_screen_point(self, latitude_deg: float, longitude_deg: float) -> QPointF:

        world_x, world_y = self._location_to_world_pixel(latitude_deg, longitude_deg, self._current_zoom_level)
        world_size = self._world_size(self._current_zoom_level)
        delta_x = world_x - self._center_world_x
        if delta_x > world_size / 2.0:
            delta_x -= world_size
        elif delta_x < -world_size / 2.0:
            delta_x += world_size
        delta_y = world_y - self._center_world_y
        return QPointF((self.width() / 2.0) + delta_x, (self.height() / 2.0) + delta_y)

    def _world_size(self, zoom_level: int) -> float:

        return float(_MAP_TILE_SIZE_PX * (1 << int(zoom_level)))

    def _wrap_world_x(self, pixel_x: float, *, zoom_level: int | None = None) -> float:

        world_size = self._world_size(self._current_zoom_level if zoom_level is None else zoom_level)
        return float(pixel_x) % world_size

    def _clamp_world_y(self, pixel_y: float, *, zoom_level: int | None = None) -> float:

        world_size = self._world_size(self._current_zoom_level if zoom_level is None else zoom_level)
        return max(0.0, min(world_size, float(pixel_y)))

    def _visible_tile_keys(self) -> list[tuple[int, int, int]]:

        if self.width() <= 0 or self.height() <= 0:
            return []
        zoom_level = self._current_zoom_level
        tile_count = 1 << zoom_level
        top_left_x = self._center_world_x - (self.width() / 2.0)
        top_left_y = self._center_world_y - (self.height() / 2.0)
        bottom_right_x = self._center_world_x + (self.width() / 2.0)
        bottom_right_y = self._center_world_y + (self.height() / 2.0)
        min_tile_x = math.floor(top_left_x / _MAP_TILE_SIZE_PX) - 1
        max_tile_x = math.floor(bottom_right_x / _MAP_TILE_SIZE_PX) + 1
        min_tile_y = max(0, math.floor(top_left_y / _MAP_TILE_SIZE_PX) - 1)
        max_tile_y = min(tile_count - 1, math.floor(bottom_right_y / _MAP_TILE_SIZE_PX) + 1)
        tile_keys: list[tuple[int, int, int]] = []
        for tile_y in range(min_tile_y, max_tile_y + 1):
            for tile_x in range(min_tile_x, max_tile_x + 1):
                tile_keys.append((zoom_level, tile_x % tile_count, tile_y))
        return tile_keys

    def _request_visible_tiles(self) -> None:

        if os.environ.get("QT_QPA_PLATFORM", "").strip().casefold() == "offscreen":
            return

        for tile_key in self._visible_tile_keys():
            if tile_key in self._tile_images or tile_key in self._tile_futures:
                continue
            zoom_level, tile_x, tile_y = tile_key
            self._tile_futures[tile_key] = _MAP_TILE_EXECUTOR.submit(_download_map_tile, zoom_level, tile_x, tile_y)
        if self._tile_futures and not self._tile_poll_timer.isActive():
            self._tile_poll_timer.start()

    def _check_tile_futures(self) -> None:

        completed_keys = [tile_key for tile_key, future in self._tile_futures.items() if future.done()]
        if not completed_keys:
            return
        for tile_key in completed_keys:
            future = self._tile_futures.pop(tile_key)
            try:
                tile_bytes = future.result()
            except Exception:
                tile_bytes = None
            if not tile_bytes:
                continue
            image = QImage()
            if image.loadFromData(tile_bytes):
                self._tile_images[tile_key] = image
        if not self._tile_futures:
            self._tile_poll_timer.stop()
        self.update()

    def _draw_tiles(self, painter: QPainter, bounds: QRectF) -> None:

        zoom_level = self._current_zoom_level
        tile_count = 1 << zoom_level
        top_left_x = self._center_world_x - (self.width() / 2.0)
        top_left_y = self._center_world_y - (self.height() / 2.0)
        for zoom, tile_x, tile_y in self._visible_tile_keys():
            if zoom != zoom_level:
                continue
            unwrapped_tile_x = math.floor(top_left_x / _MAP_TILE_SIZE_PX) - 1
            while unwrapped_tile_x % tile_count != tile_x:
                unwrapped_tile_x += 1
            tile_screen_x = (unwrapped_tile_x * _MAP_TILE_SIZE_PX) - top_left_x
            tile_screen_y = (tile_y * _MAP_TILE_SIZE_PX) - top_left_y
            tile_rect = QRectF(tile_screen_x, tile_screen_y, _MAP_TILE_SIZE_PX, _MAP_TILE_SIZE_PX)
            image = self._tile_images.get((zoom, tile_x, tile_y))
            if image is not None and not image.isNull():
                painter.drawImage(tile_rect, image)
            else:
                painter.fillRect(tile_rect, QColor(8, 25, 46, 232))

    def _draw_map_overlays(self, painter: QPainter, bounds: QRectF) -> None:

        painter.save()
        painter.setPen(QPen(QColor(125, 211, 252, 22), 1.0))
        grid_spacing = 128
        x_value = 0
        while x_value <= self.width():
            painter.drawLine(QPointF(float(x_value), 0.0), QPointF(float(x_value), float(self.height())))
            x_value += grid_spacing
        y_value = 0
        while y_value <= self.height():
            painter.drawLine(QPointF(0.0, float(y_value)), QPointF(float(self.width()), float(y_value)))
            y_value += grid_spacing
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QColor(2, 7, 19, 34))
        painter.drawRect(bounds)
        painter.setBrush(Qt.BrushStyle.NoBrush)
        painter.setPen(QPen(QColor(125, 211, 252, 72), 1.0))
        painter.drawRoundedRect(bounds.adjusted(0.5, 0.5, -0.5, -0.5), 18.0, 18.0)
        painter.restore()

    def _draw_selection_marker(self, painter: QPainter, bounds: QRectF) -> None:

        marker_point = self._location_to_screen_point(self._selected_latitude_deg, self._selected_longitude_deg)
        if not bounds.adjusted(-24.0, -24.0, 24.0, 24.0).contains(marker_point):
            return
        painter.save()
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QColor(56, 189, 248, 30))
        painter.drawEllipse(marker_point, 34.0, 34.0)
        painter.setBrush(QColor(56, 189, 248, 72))
        painter.drawEllipse(marker_point, 21.0, 21.0)
        painter.setBrush(Qt.BrushStyle.NoBrush)
        painter.setPen(QPen(QColor(191, 219, 254, 155), 1.2))
        painter.drawLine(QPointF(marker_point.x() - 25.0, marker_point.y()), QPointF(marker_point.x() - 10.0, marker_point.y()))
        painter.drawLine(QPointF(marker_point.x() + 10.0, marker_point.y()), QPointF(marker_point.x() + 25.0, marker_point.y()))
        painter.drawLine(QPointF(marker_point.x(), marker_point.y() - 25.0), QPointF(marker_point.x(), marker_point.y() - 10.0))
        painter.drawLine(QPointF(marker_point.x(), marker_point.y() + 10.0), QPointF(marker_point.x(), marker_point.y() + 25.0))
        painter.setBrush(QColor(14, 165, 233, 245))
        painter.setPen(QPen(QColor("#f8fafc"), 2.2))
        painter.drawEllipse(marker_point, 8.0, 8.0)
        painter.setBrush(Qt.BrushStyle.NoBrush)
        painter.setPen(QPen(QColor(191, 219, 254, 210), 1.4))
        painter.drawEllipse(marker_point, 14.0, 14.0)
        label_text = f"{self._selected_latitude_deg:.4f}, {self._selected_longitude_deg:.4f}"
        font = painter.font()
        font.setPointSize(9)
        font.setBold(True)
        painter.setFont(font)
        metrics = painter.fontMetrics()
        label_width = float(metrics.horizontalAdvance(label_text) + 18)
        label_height = float(metrics.height() + 8)
        label_x = min(max(marker_point.x() + 20.0, bounds.left() + 10.0), bounds.right() - label_width - 10.0)
        label_y = marker_point.y() - label_height - 16.0
        if label_y < bounds.top() + 10.0:
            label_y = marker_point.y() + 20.0
        label_y = min(max(label_y, bounds.top() + 10.0), bounds.bottom() - label_height - 10.0)
        label_rect = QRectF(label_x, label_y, label_width, label_height)
        painter.setPen(QPen(QColor(125, 211, 252, 135), 1.0))
        painter.setBrush(QColor(3, 12, 25, 224))
        painter.drawRoundedRect(label_rect, 8.0, 8.0)
        painter.setPen(QColor("#e0f2fe"))
        painter.drawText(label_rect, Qt.AlignmentFlag.AlignCenter, label_text)
        painter.restore()


class _SkyViewLocationDialog(QDialog):

    def __init__(self, settings: AppSettings | None, parent: QWidget | None = None) -> None:

        super().__init__(parent)

        self.setWindowModality(Qt.WindowModality.WindowModal)
        self.setWindowTitle("Observer Location")
        self.resize(1160, 740)
        self.setObjectName("skyViewLocationDialog")

        current_latitude = 0.0 if settings is None or settings.observing_site_latitude_deg is None else float(settings.observing_site_latitude_deg)
        current_longitude = 0.0 if settings is None or settings.observing_site_longitude_deg is None else float(settings.observing_site_longitude_deg)
        current_elevation = None if settings is None else settings.observing_site_elevation_m

        self._site_presets: list[ObservingSitePreset] = list(settings.observing_site_presets or ()) if settings is not None else []
        self._reverse_geocode_executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="sky-view-geocode")
        self._reverse_geocode_future: Future[str | None] | None = None
        self._reverse_geocode_request_id = 0
        self._site_name_autofill_enabled = not bool(settings is not None and settings.site_name.strip())
        self._last_autofill_name = ""

        self._reverse_geocode_debounce_timer = QTimer(self)
        self._reverse_geocode_debounce_timer.setInterval(500)
        self._reverse_geocode_debounce_timer.setSingleShot(True)
        self._reverse_geocode_debounce_timer.timeout.connect(self._start_reverse_geocode_request)

        self._reverse_geocode_poll_timer = QTimer(self)
        self._reverse_geocode_poll_timer.setInterval(150)
        self._reverse_geocode_poll_timer.timeout.connect(self._check_reverse_geocode_result)

        layout = QHBoxLayout()
        layout.setContentsMargins(22, 22, 22, 22)
        layout.setSpacing(18)

        map_column = QVBoxLayout()
        map_column.setContentsMargins(0, 0, 0, 0)
        map_column.setSpacing(12)

        title_label = QLabel("Observer Location")
        title_label.setObjectName("skyViewLocationTitle")

        subtitle_label = QLabel(
            "Pick your observing site on an exact Earth map. The live tiles stay precise, while the dark blue grading and overlays keep the surface aligned with Sky View’s design."
        )
        subtitle_label.setObjectName("skyViewLocationSubtitle")
        subtitle_label.setWordWrap(True)

        map_column.addWidget(title_label)
        map_column.addWidget(subtitle_label)

        self._location_map_widget = _SkyViewLocationMapWidget(current_latitude, current_longitude, self)
        map_column.addWidget(self._location_map_widget, stretch=1)

        map_controls_row = QHBoxLayout()
        map_controls_row.setContentsMargins(0, 0, 0, 0)
        map_controls_row.setSpacing(8)

        self._zoom_out_button = QPushButton("-")
        self._zoom_out_button.setFixedWidth(40)
        self._zoom_out_button.clicked.connect(self._location_map_widget.zoom_out)

        self._zoom_in_button = QPushButton("+")
        self._zoom_in_button.setFixedWidth(40)
        self._zoom_in_button.clicked.connect(self._location_map_widget.zoom_in)

        self._reset_view_button = QPushButton("Center on marker")
        self._reset_view_button.clicked.connect(self._location_map_widget.reset_view)

        map_controls_row.addWidget(self._zoom_out_button)
        map_controls_row.addWidget(self._zoom_in_button)
        map_controls_row.addWidget(self._reset_view_button)
        map_controls_row.addStretch(1)

        map_column.addLayout(map_controls_row)

        side_card = QWidget(self)
        side_card.setObjectName("skyViewLocationSideCard")
        side_card.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)

        side_layout = QVBoxLayout()
        side_layout.setContentsMargins(18, 18, 18, 18)
        side_layout.setSpacing(12)

        summary_label = QLabel("Saved Site")
        summary_label.setObjectName("skyViewLocationSectionTitle")
        side_layout.addWidget(summary_label)

        form_layout = QFormLayout()
        form_layout.setContentsMargins(0, 0, 0, 0)
        form_layout.setHorizontalSpacing(12)
        form_layout.setVerticalSpacing(10)

        self._site_name_input = QLineEdit()
        self._site_name_input.setPlaceholderText("Site name or auto-filled place label")
        if settings is not None and settings.site_name.strip():
            self._site_name_input.setText(settings.site_name.strip())
        form_layout.addRow("Name", self._site_name_input)

        self._site_name_status_label = QLabel("")
        self._site_name_status_label.setObjectName("skyViewLocationHint")
        form_layout.addRow("", self._site_name_status_label)

        self._latitude_spin = QDoubleSpinBox()
        self._latitude_spin.setRange(-90.0, 90.0)
        self._latitude_spin.setDecimals(5)
        self._latitude_spin.setSingleStep(0.1)
        self._latitude_spin.setSuffix(" deg")
        self._latitude_spin.setButtonSymbols(QAbstractSpinBox.ButtonSymbols.NoButtons)
        self._latitude_spin.setKeyboardTracking(False)
        self._latitude_spin.setValue(current_latitude)
        form_layout.addRow("Latitude", self._latitude_spin)

        self._longitude_spin = QDoubleSpinBox()
        self._longitude_spin.setRange(-180.0, 180.0)
        self._longitude_spin.setDecimals(5)
        self._longitude_spin.setSingleStep(0.1)
        self._longitude_spin.setSuffix(" deg")
        self._longitude_spin.setButtonSymbols(QAbstractSpinBox.ButtonSymbols.NoButtons)
        self._longitude_spin.setKeyboardTracking(False)
        self._longitude_spin.setValue(current_longitude)
        form_layout.addRow("Longitude", self._longitude_spin)

        self._elevation_spin = QDoubleSpinBox()
        self._elevation_spin.setRange(-500.0, 12000.0)
        self._elevation_spin.setDecimals(1)
        self._elevation_spin.setSingleStep(10.0)
        self._elevation_spin.setSuffix(" m")
        self._elevation_spin.setButtonSymbols(QAbstractSpinBox.ButtonSymbols.NoButtons)
        self._elevation_spin.setKeyboardTracking(False)
        self._elevation_spin.setValue(0.0 if current_elevation is None else float(current_elevation))
        form_layout.addRow("Elevation", self._elevation_spin)

        side_layout.addLayout(form_layout)

        self._save_to_presets_checkbox = QCheckBox("Keep this site in quick-switch presets")
        self._save_to_presets_checkbox.setChecked(True)
        side_layout.addWidget(self._save_to_presets_checkbox)

        presets_label = QLabel("Saved Sites")
        presets_label.setObjectName("skyViewLocationSectionTitle")
        side_layout.addWidget(presets_label)

        presets_row = QHBoxLayout()
        presets_row.setContentsMargins(0, 0, 0, 0)
        presets_row.setSpacing(8)

        self._saved_sites_combo = QComboBox()
        presets_row.addWidget(self._saved_sites_combo, stretch=1)

        self._load_saved_site_button = QPushButton("Load")
        self._load_saved_site_button.clicked.connect(self._load_selected_saved_site)
        presets_row.addWidget(self._load_saved_site_button)

        self._delete_saved_site_button = QPushButton("Remove")
        self._delete_saved_site_button.clicked.connect(self._delete_selected_saved_site)
        presets_row.addWidget(self._delete_saved_site_button)

        side_layout.addLayout(presets_row)

        self._saved_sites_hint_label = QLabel("Use the list above to switch between saved observing sites before saving one as active.")
        self._saved_sites_hint_label.setObjectName("skyViewLocationHint")
        self._saved_sites_hint_label.setWordWrap(True)
        side_layout.addWidget(self._saved_sites_hint_label)

        self._selection_summary_label = QLabel("")
        self._selection_summary_label.setObjectName("skyViewLocationSummary")
        self._selection_summary_label.setWordWrap(True)
        side_layout.addWidget(self._selection_summary_label)

        note_label = QLabel(
            "Clicking the map can auto-fill a place label from the selected coordinates. Manual edits stay respected until you clear the name field again."
        )
        note_label.setObjectName("skyViewLocationNote")
        note_label.setWordWrap(True)
        side_layout.addWidget(note_label)

        side_layout.addStretch(1)

        button_box = QDialogButtonBox(QDialogButtonBox.StandardButton.Save | QDialogButtonBox.StandardButton.Cancel, parent=self)
        save_button = button_box.button(QDialogButtonBox.StandardButton.Save)
        if save_button is not None:
            save_button.setText("Save Location")
            save_button.setObjectName("skyViewLocationSaveButton")
        button_box.accepted.connect(self._handle_accept)
        button_box.rejected.connect(self.reject)
        side_layout.addWidget(button_box)

        side_card.setLayout(side_layout)

        layout.addLayout(map_column, stretch=3)
        layout.addWidget(side_card, stretch=2)
        self.setLayout(layout)

        self.setStyleSheet(
            """
            #skyViewLocationDialog {
                background-color: #020713;
            }
            #skyViewLocationTitle {
                color: #f8fafc;
                font-size: 22px;
                font-weight: 700;
            }
            #skyViewLocationSubtitle {
                color: rgba(219, 234, 254, 0.85);
                font-size: 12px;
            }
            #skyViewLocationSideCard {
                background-color: rgba(4, 13, 27, 228);
                border: 1px solid rgba(148, 163, 184, 80);
                border-radius: 18px;
            }
            #skyViewLocationSectionTitle {
                color: #f8fafc;
                font-size: 15px;
                font-weight: 700;
            }
            #skyViewLocationSummary {
                background-color: rgba(15, 23, 42, 200);
                border: 1px solid rgba(125, 211, 252, 72);
                border-radius: 12px;
                color: #dbeafe;
                padding: 10px 12px;
                font-size: 12px;
                font-weight: 600;
            }
            #skyViewLocationNote, #skyViewLocationHint {
                color: rgba(191, 219, 254, 0.76);
                font-size: 11px;
            }
            QLineEdit, QDoubleSpinBox, QComboBox {
                background-color: rgba(15, 23, 42, 220);
                border: 1px solid rgba(148, 163, 184, 110);
                border-radius: 8px;
                color: #f8fafc;
                min-height: 34px;
                padding: 4px 8px;
            }
            QDoubleSpinBox {
                padding-right: 10px;
            }
            QCheckBox, QLabel {
                color: #dbeafe;
            }
            QPushButton {
                background-color: rgba(30, 41, 59, 220);
                border: 1px solid rgba(148, 163, 184, 115);
                border-radius: 8px;
                color: #f8fafc;
                min-height: 34px;
                padding: 6px 12px;
            }
            QPushButton:hover {
                background-color: rgba(51, 65, 85, 235);
            }
            QPushButton#skyViewLocationSaveButton {
                background-color: rgba(8, 47, 73, 235);
                border-color: rgba(125, 211, 252, 165);
            }
            """
        )

        self._location_map_widget.selectionChanged.connect(self._handle_map_selection_changed)
        self._site_name_input.textEdited.connect(self._handle_site_name_text_edited)
        self._site_name_input.textChanged.connect(self._update_selection_summary)
        self._latitude_spin.valueChanged.connect(self._handle_coordinate_spin_changed)
        self._longitude_spin.valueChanged.connect(self._handle_coordinate_spin_changed)

        self._refresh_saved_sites_combo()
        self._update_selection_summary()
        self._queue_reverse_geocode()

    def selection(self) -> _SkyViewObservingSiteSelection:

        return _SkyViewObservingSiteSelection(
            site_name=self._site_name_input.text().strip(),
            latitude_deg=float(self._latitude_spin.value()),
            longitude_deg=float(self._longitude_spin.value()),
            elevation_m=float(self._elevation_spin.value()),
        )

    def saved_site_presets(self) -> tuple[ObservingSitePreset, ...]:

        return tuple(self._site_presets)

    def closeEvent(self, event: QCloseEvent) -> None:

        self._reverse_geocode_debounce_timer.stop()
        self._reverse_geocode_poll_timer.stop()
        self._reverse_geocode_executor.shutdown(wait=False, cancel_futures=True)
        super().closeEvent(event)

    def _handle_map_selection_changed(self, latitude_deg: float, longitude_deg: float) -> None:

        self._latitude_spin.blockSignals(True)
        self._longitude_spin.blockSignals(True)
        self._latitude_spin.setValue(float(latitude_deg))
        self._longitude_spin.setValue(float(longitude_deg))
        self._latitude_spin.blockSignals(False)
        self._longitude_spin.blockSignals(False)
        self._queue_reverse_geocode()
        self._update_selection_summary()

    def _handle_coordinate_spin_changed(self, _value: float) -> None:

        self._location_map_widget.set_selected_location(
            float(self._latitude_spin.value()),
            float(self._longitude_spin.value()),
            recenter=False,
        )
        self._queue_reverse_geocode()
        self._update_selection_summary()

    def _handle_site_name_text_edited(self, text: str) -> None:

        normalized = text.strip()
        self._site_name_autofill_enabled = not bool(normalized) or normalized == self._last_autofill_name
        if not normalized:
            self._site_name_autofill_enabled = True
            self._queue_reverse_geocode()
        self._update_selection_summary()

    def _queue_reverse_geocode(self) -> None:

        if not self._site_name_autofill_enabled:
            self._site_name_status_label.setText("")
            return
        self._site_name_status_label.setText("Resolving place label from coordinates...")
        self._reverse_geocode_debounce_timer.start()

    def _start_reverse_geocode_request(self) -> None:

        if not self._site_name_autofill_enabled:
            return
        latitude_deg = float(self._latitude_spin.value())
        longitude_deg = float(self._longitude_spin.value())
        self._reverse_geocode_request_id += 1
        request_id = self._reverse_geocode_request_id
        self._reverse_geocode_future = self._reverse_geocode_executor.submit(_reverse_geocode_observing_site, latitude_deg, longitude_deg)
        self._reverse_geocode_poll_timer.start()
        self._site_name_status_label.setText("Resolving place label from coordinates...")
        self._active_reverse_geocode_request_id = request_id

    def _check_reverse_geocode_result(self) -> None:

        future = self._reverse_geocode_future
        if future is None or not future.done():
            return
        self._reverse_geocode_poll_timer.stop()
        self._reverse_geocode_future = None
        try:
            resolved_name = future.result()
        except Exception:
            resolved_name = None
        if not self._site_name_autofill_enabled:
            self._site_name_status_label.setText("")
            return
        if resolved_name:
            self._last_autofill_name = resolved_name
            self._site_name_input.blockSignals(True)
            self._site_name_input.setText(resolved_name)
            self._site_name_input.blockSignals(False)
            self._site_name_status_label.setText("Auto-filled from clicked coordinates.")
        else:
            self._site_name_status_label.setText("No place label found for these coordinates.")
        self._update_selection_summary()

    def _refresh_saved_sites_combo(self) -> None:

        current_text = self._saved_sites_combo.currentText()
        self._saved_sites_combo.blockSignals(True)
        self._saved_sites_combo.clear()
        for preset in self._site_presets:
            display_name = _observing_site_display_name(preset.name, preset.latitude_deg, preset.longitude_deg)
            self._saved_sites_combo.addItem(display_name)
        self._saved_sites_combo.blockSignals(False)
        if current_text:
            restored_index = self._saved_sites_combo.findText(current_text)
            if restored_index >= 0:
                self._saved_sites_combo.setCurrentIndex(restored_index)
        has_presets = bool(self._site_presets)
        self._saved_sites_combo.setEnabled(has_presets)
        self._load_saved_site_button.setEnabled(has_presets)
        self._delete_saved_site_button.setEnabled(has_presets)
        if not has_presets:
            self._saved_sites_combo.setPlaceholderText("No saved sites yet")

    def _load_selected_saved_site(self) -> None:

        preset_index = self._saved_sites_combo.currentIndex()
        if preset_index < 0 or preset_index >= len(self._site_presets):
            return
        preset = self._site_presets[preset_index]
        self._site_name_input.blockSignals(True)
        self._site_name_input.setText(preset.name)
        self._site_name_input.blockSignals(False)
        self._site_name_autofill_enabled = not bool(preset.name.strip())
        self._latitude_spin.setValue(float(preset.latitude_deg))
        self._longitude_spin.setValue(float(preset.longitude_deg))
        self._elevation_spin.setValue(0.0 if preset.elevation_m is None else float(preset.elevation_m))
        self._location_map_widget.set_selected_location(preset.latitude_deg, preset.longitude_deg, recenter=True)
        self._site_name_status_label.setText("Loaded a saved site preset.")
        self._update_selection_summary()

    def _delete_selected_saved_site(self) -> None:

        preset_index = self._saved_sites_combo.currentIndex()
        if preset_index < 0 or preset_index >= len(self._site_presets):
            return
        del self._site_presets[preset_index]
        self._refresh_saved_sites_combo()
        self._site_name_status_label.setText("Removed the selected saved site.")

    def _handle_accept(self) -> None:

        if self._save_to_presets_checkbox.isChecked():
            self._upsert_current_selection_as_preset()
        self.accept()

    def _upsert_current_selection_as_preset(self) -> None:

        selection = self.selection()
        preset = ObservingSitePreset(
            name=selection.site_name,
            latitude_deg=selection.latitude_deg,
            longitude_deg=selection.longitude_deg,
            elevation_m=selection.elevation_m,
        )
        replaced = False
        if preset.name.strip():
            for index, existing_preset in enumerate(self._site_presets):
                if existing_preset.name.strip().casefold() == preset.name.strip().casefold():
                    self._site_presets[index] = preset
                    replaced = True
                    break
        if not replaced:
            for index, existing_preset in enumerate(self._site_presets):
                if (
                    abs(existing_preset.latitude_deg - preset.latitude_deg) <= 1.0e-6
                    and abs(existing_preset.longitude_deg - preset.longitude_deg) <= 1.0e-6
                ):
                    self._site_presets[index] = preset
                    replaced = True
                    break
        if not replaced:
            self._site_presets.append(preset)
        self._refresh_saved_sites_combo()

    def _update_selection_summary(self) -> None:

        latitude_deg = float(self._latitude_spin.value())
        longitude_deg = float(self._longitude_spin.value())
        latitude_suffix = "N" if latitude_deg >= 0.0 else "S"
        longitude_suffix = "E" if longitude_deg >= 0.0 else "W"
        site_name = self._site_name_input.text().strip()
        site_label = _observing_site_display_name(site_name, latitude_deg, longitude_deg)
        self._selection_summary_label.setText(
            f"{site_label}\n{abs(latitude_deg):.4f} deg {latitude_suffix}, {abs(longitude_deg):.4f} deg {longitude_suffix}\n"
            f"Elevation {float(self._elevation_spin.value()):.1f} m"
        )