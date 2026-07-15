from __future__ import annotations

"""Fast d3-celestial stick-figure constellation overlay.

Official IAU/VizieR boundaries are intentionally left for a separate future
overlay so object lookup and boundary toggles do not couple to stick figures.
"""

from photometry_app.core.benchmarking import BENCHMARK_ENABLED, get_benchmark_recorder

import json
import math
from collections import OrderedDict
from dataclasses import dataclass
from pathlib import Path
from time import perf_counter
from typing import Any

import numpy as np
from PySide6.QtCore import QLineF, QPointF, QRectF, Qt
from PySide6.QtGui import QColor, QFont, QPainter, QPen, QStaticText, QTransform


@dataclass(frozen=True, slots=True)
class ConstellationLineSegment:
    constellation_id: str
    start_ra_deg: float
    start_dec_deg: float
    end_ra_deg: float
    end_dec_deg: float
    start_unit_vector: tuple[float, float, float]
    end_unit_vector: tuple[float, float, float]


@dataclass(frozen=True, slots=True)
class ConstellationLabel:
    constellation_id: str
    name: str
    abbreviation: str
    anchor_ra_deg: float
    anchor_dec_deg: float
    anchor_unit_vector: tuple[float, float, float]
    rank: int = 3


@dataclass(frozen=True, slots=True)
class ConstellationOverlaySettings:
    enabled: bool = False
    show_lines: bool = True
    show_labels: bool = True
    line_color: str = "#dfe7f2"
    line_opacity: float = 0.42
    line_width_px: float = 1.0
    label_color: str = "#dfe7f2"
    label_opacity: float = 0.72
    label_size_pt: float = 11.0
    label_full_opacity_field_width_deg: float = 82.0
    label_hidden_field_width_deg: float = 148.0


@dataclass(frozen=True, slots=True)
class ConstellationOverlayMetrics:
    line_segment_count: int = 0
    drawn_line_count: int = 0
    label_count: int = 0
    drawn_label_count: int = 0
    projected_line_count: int = 0
    projected_label_count: int = 0
    label_text_cache_hits: int = 0
    label_text_cache_misses: int = 0
    overlay_seconds: float = 0.0
    cache_status: str = "disabled"
    data_cache_status: str = "not-loaded"


@dataclass(frozen=True, slots=True)
class ConstellationOverlayData:
    source_key: tuple[object, ...]
    line_segments: tuple[ConstellationLineSegment, ...]
    labels: tuple[ConstellationLabel, ...]
    line_unit_vectors: np.ndarray
    label_unit_vectors: np.ndarray


@dataclass(frozen=True, slots=True)
class _ProjectedConstellationLabel:
    label: ConstellationLabel
    x: float
    y: float


@dataclass(frozen=True, slots=True)
class _ProjectedConstellationGeometry:
    lines: tuple[QLineF, ...]
    labels: tuple[_ProjectedConstellationLabel, ...]
    cache_status: str
    segments_considered: int = 0
    labels_considered: int = 0


class ConstellationDataLoader:
    def __init__(self, data_dir: Path | None = None) -> None:
        self._data_dir = Path(data_dir) if data_dir is not None else Path(__file__).resolve().parents[1] / "data"
        self._cached_source_key: tuple[object, ...] | None = None
        self._cached_data: ConstellationOverlayData | None = None
        self.last_cache_status = "not-loaded"

    @property
    def data_dir(self) -> Path:
        return self._data_dir

    def load(self) -> ConstellationOverlayData:
        benchmark_recorder = get_benchmark_recorder() if BENCHMARK_ENABLED else None

        benchmark_token = benchmark_recorder.start_section("constellation.data_load") if benchmark_recorder is not None else None
        lines_path = self._data_dir / "constellations.lines.json"
        labels_path = self._data_dir / "constellations.json"
        source_key = self._source_key(lines_path, labels_path)
        if self._cached_data is not None and self._cached_source_key == source_key:
            self.last_cache_status = "hit"
            if benchmark_recorder is not None:
                benchmark_recorder.stop_section(benchmark_token, metadata={"cache_status": "hit"})
            return self._cached_data

        lines_payload = self._read_json(lines_path)
        labels_payload = self._read_json(labels_path) if labels_path.exists() else {}
        line_segments, endpoint_vectors_by_constellation = self._parse_line_segments(lines_payload)
        labels = self._parse_labels(labels_payload, endpoint_vectors_by_constellation)

        line_vectors = np.empty((len(line_segments), 2, 3), dtype=np.float32)
        for index, segment in enumerate(line_segments):
            line_vectors[index, 0, :] = segment.start_unit_vector
            line_vectors[index, 1, :] = segment.end_unit_vector

        label_vectors = np.empty((len(labels), 3), dtype=np.float32)
        for index, label in enumerate(labels):
            label_vectors[index, :] = label.anchor_unit_vector

        data = ConstellationOverlayData(
            source_key=source_key,
            line_segments=tuple(line_segments),
            labels=tuple(labels),
            line_unit_vectors=line_vectors,
            label_unit_vectors=label_vectors,
        )
        self._cached_source_key = source_key
        self._cached_data = data
        self.last_cache_status = "miss"
        if benchmark_recorder is not None:
            benchmark_recorder.stop_section(
                benchmark_token,
                metadata={"cache_status": "miss", "line_segments": len(line_segments), "labels": len(labels)},
            )
        return data

    @staticmethod
    def _read_json(path: Path) -> dict[str, Any]:
        with path.open("r", encoding="utf-8") as handle:
            payload = json.load(handle)
        if not isinstance(payload, dict):
            raise ValueError(f"Expected a JSON object in {path}")
        return payload

    @staticmethod
    def _source_key(lines_path: Path, labels_path: Path) -> tuple[object, ...]:
        paths = (lines_path, labels_path)
        key_parts: list[object] = []
        for path in paths:
            try:
                stat = path.stat()
            except OSError:
                key_parts.extend((str(path), None, None))
            else:
                key_parts.extend((str(path), int(stat.st_mtime_ns), int(stat.st_size)))
        return tuple(key_parts)

    @classmethod
    def _parse_line_segments(
        cls,
        payload: dict[str, Any],
    ) -> tuple[list[ConstellationLineSegment], dict[str, list[tuple[float, float, float]]]]:
        features = payload.get("features", [])
        if not isinstance(features, list):
            raise ValueError("constellations.lines.json must contain a GeoJSON features list")

        segments: list[ConstellationLineSegment] = []
        endpoint_vectors_by_constellation: dict[str, list[tuple[float, float, float]]] = {}
        for feature in features:
            if not isinstance(feature, dict):
                continue
            constellation_id = str(feature.get("id") or "").strip()
            if not constellation_id:
                continue
            geometry = feature.get("geometry")
            if not isinstance(geometry, dict) or geometry.get("type") != "MultiLineString":
                continue
            coordinates = geometry.get("coordinates", [])
            if not isinstance(coordinates, list):
                continue
            endpoint_vectors = endpoint_vectors_by_constellation.setdefault(constellation_id, [])
            for line_string in coordinates:
                if not isinstance(line_string, list) or len(line_string) < 2:
                    continue
                parsed_points = [cls._parse_coordinate_pair(point) for point in line_string]
                parsed_points = [point for point in parsed_points if point is not None]
                for start, end in zip(parsed_points, parsed_points[1:]):
                    start_ra_deg, start_dec_deg = start
                    end_ra_deg, end_dec_deg = end
                    start_unit_vector = cls._equatorial_unit_vector(start_ra_deg, start_dec_deg)
                    end_unit_vector = cls._equatorial_unit_vector(end_ra_deg, end_dec_deg)
                    if cls._angular_separation_deg(start_unit_vector, end_unit_vector) <= 1.0e-6:
                        continue
                    endpoint_vectors.extend((start_unit_vector, end_unit_vector))
                    segments.append(
                        ConstellationLineSegment(
                            constellation_id=constellation_id,
                            start_ra_deg=start_ra_deg % 360.0,
                            start_dec_deg=start_dec_deg,
                            end_ra_deg=end_ra_deg % 360.0,
                            end_dec_deg=end_dec_deg,
                            start_unit_vector=start_unit_vector,
                            end_unit_vector=end_unit_vector,
                        )
                    )
        return segments, endpoint_vectors_by_constellation

    @classmethod
    def _parse_labels(
        cls,
        payload: dict[str, Any],
        endpoint_vectors_by_constellation: dict[str, list[tuple[float, float, float]]],
    ) -> list[ConstellationLabel]:
        labels_by_id: dict[str, ConstellationLabel] = {}
        features = payload.get("features", [])
        if isinstance(features, list):
            for feature in features:
                if not isinstance(feature, dict):
                    continue
                constellation_id = str(feature.get("id") or "").strip()
                if not constellation_id:
                    continue
                properties = feature.get("properties")
                if not isinstance(properties, dict):
                    properties = {}
                name = str(properties.get("name") or properties.get("en") or constellation_id).replace("\u2005", " ").strip()
                abbreviation = str(properties.get("desig") or constellation_id).strip()
                rank = cls._safe_int(properties.get("rank"), default=3)
                anchor = cls._label_anchor_from_properties(properties)
                if anchor is None:
                    geometry = feature.get("geometry")
                    if isinstance(geometry, dict) and geometry.get("type") == "Point":
                        anchor = cls._parse_coordinate_pair(geometry.get("coordinates"))
                if anchor is None:
                    continue
                anchor_ra_deg, anchor_dec_deg = anchor
                labels_by_id[constellation_id] = ConstellationLabel(
                    constellation_id=constellation_id,
                    name=name,
                    abbreviation=abbreviation,
                    anchor_ra_deg=anchor_ra_deg % 360.0,
                    anchor_dec_deg=anchor_dec_deg,
                    anchor_unit_vector=cls._equatorial_unit_vector(anchor_ra_deg, anchor_dec_deg),
                    rank=rank,
                )

        for constellation_id, endpoint_vectors in endpoint_vectors_by_constellation.items():
            if constellation_id in labels_by_id or not endpoint_vectors:
                continue
            anchor_vector = cls._average_unit_vector(endpoint_vectors)
            anchor_ra_deg, anchor_dec_deg = cls._unit_vector_to_ra_dec(anchor_vector)
            labels_by_id[constellation_id] = ConstellationLabel(
                constellation_id=constellation_id,
                name=constellation_id,
                abbreviation=constellation_id,
                anchor_ra_deg=anchor_ra_deg,
                anchor_dec_deg=anchor_dec_deg,
                anchor_unit_vector=anchor_vector,
                rank=3,
            )

        return sorted(labels_by_id.values(), key=lambda label: (label.rank, label.name.casefold(), label.constellation_id))

    @staticmethod
    def _label_anchor_from_properties(properties: dict[str, Any]) -> tuple[float, float] | None:
        display = properties.get("display")
        if isinstance(display, list) and len(display) >= 2:
            return ConstellationDataLoader._parse_coordinate_pair(display[:2])
        return None

    @staticmethod
    def _parse_coordinate_pair(value: Any) -> tuple[float, float] | None:
        if not isinstance(value, (list, tuple)) or len(value) < 2:
            return None
        try:
            ra_deg = float(value[0]) % 360.0
            dec_deg = max(-90.0, min(90.0, float(value[1])))
        except (TypeError, ValueError):
            return None
        if not math.isfinite(ra_deg) or not math.isfinite(dec_deg):
            return None
        return ra_deg, dec_deg

    @staticmethod
    def _safe_int(value: Any, *, default: int) -> int:
        try:
            return int(value)
        except (TypeError, ValueError):
            return default

    @staticmethod
    def _equatorial_unit_vector(ra_deg: float, dec_deg: float) -> tuple[float, float, float]:
        ra_rad = math.radians(float(ra_deg) % 360.0)
        dec_rad = math.radians(max(-90.0, min(90.0, float(dec_deg))))
        cos_dec = math.cos(dec_rad)
        return (cos_dec * math.cos(ra_rad), cos_dec * math.sin(ra_rad), math.sin(dec_rad))

    @staticmethod
    def _unit_vector_to_ra_dec(vector: tuple[float, float, float]) -> tuple[float, float]:
        x_value, y_value, z_value = ConstellationDataLoader._normalize_vector(vector)
        return math.degrees(math.atan2(y_value, x_value)) % 360.0, math.degrees(math.asin(max(-1.0, min(1.0, z_value))))

    @staticmethod
    def _average_unit_vector(vectors: list[tuple[float, float, float]]) -> tuple[float, float, float]:
        if not vectors:
            return (1.0, 0.0, 0.0)
        total = (sum(vector[0] for vector in vectors), sum(vector[1] for vector in vectors), sum(vector[2] for vector in vectors))
        return ConstellationDataLoader._normalize_vector(total)

    @staticmethod
    def _normalize_vector(vector: tuple[float, float, float]) -> tuple[float, float, float]:
        vector_length = math.sqrt(vector[0] * vector[0] + vector[1] * vector[1] + vector[2] * vector[2])
        if vector_length <= 1.0e-9:
            return (1.0, 0.0, 0.0)
        return (vector[0] / vector_length, vector[1] / vector_length, vector[2] / vector_length)

    @staticmethod
    def _angular_separation_deg(lhs: tuple[float, float, float], rhs: tuple[float, float, float]) -> float:
        dot_value = max(-1.0, min(1.0, lhs[0] * rhs[0] + lhs[1] * rhs[1] + lhs[2] * rhs[2]))
        return math.degrees(math.acos(dot_value))


class ConstellationLineRenderer:
    def draw(self, painter: QPainter, lines: tuple[QLineF, ...], settings: ConstellationOverlaySettings) -> int:
        benchmark_recorder = get_benchmark_recorder() if BENCHMARK_ENABLED else None

        benchmark_batch_token = benchmark_recorder.start_section("constellation.line_batching", metadata={"lines": len(lines)}) if benchmark_recorder is not None else None

        if not lines:
            if benchmark_recorder is not None:
                benchmark_recorder.stop_section(benchmark_batch_token, metadata={"lines": 0})
            return 0
        color = QColor(settings.line_color)
        if not color.isValid():
            color = QColor("#dfe7f2")
        color.setAlphaF(max(0.0, min(1.0, float(settings.line_opacity))))
        pen = QPen(color, max(0.25, min(6.0, float(settings.line_width_px))))
        pen.setCapStyle(Qt.PenCapStyle.RoundCap)
        pen.setJoinStyle(Qt.PenJoinStyle.RoundJoin)
        pen.setCosmetic(True)
        painter.setPen(pen)
        painter.setBrush(Qt.BrushStyle.NoBrush)
        if benchmark_recorder is not None:
            benchmark_recorder.stop_section(benchmark_batch_token, metadata={"lines": len(lines)})
        benchmark_draw_token = benchmark_recorder.start_section("constellation.line_drawing", metadata={"lines": len(lines)}) if benchmark_recorder is not None else None
        painter.drawLines(lines)
        if benchmark_recorder is not None:
            benchmark_recorder.stop_section(benchmark_draw_token, metadata={"lines": len(lines)})
        return len(lines)


class ConstellationLabelRenderer:
    def __init__(self) -> None:
        self._static_text_cache: OrderedDict[tuple[str, float], tuple[QStaticText, float, float]] = OrderedDict()
        self._cache_limit = 256
        self.last_cache_hits = 0
        self.last_cache_misses = 0

    def draw(
        self,
        painter: QPainter,
        labels: tuple[_ProjectedConstellationLabel, ...],
        settings: ConstellationOverlaySettings,
        field_width_deg: float,
    ) -> int:
        benchmark_recorder = get_benchmark_recorder() if BENCHMARK_ENABLED else None

        self.last_cache_hits = 0
        self.last_cache_misses = 0
        benchmark_visibility_token = benchmark_recorder.start_section("constellation.label_visibility_fade", metadata={"labels": len(labels)}) if benchmark_recorder is not None else None
        label_alpha = self._label_alpha(settings, field_width_deg)
        if benchmark_recorder is not None:
            benchmark_recorder.stop_section(benchmark_visibility_token, metadata={"label_alpha": label_alpha})
        if not labels or label_alpha <= 0.01:
            return 0

        color = QColor(settings.label_color)
        if not color.isValid():
            color = QColor(settings.line_color)
        if not color.isValid():
            color = QColor("#dfe7f2")
        color.setAlphaF(label_alpha)
        shadow_color = QColor(0, 0, 0, max(36, min(150, int(round(180.0 * label_alpha)))))
        font = self._label_font(settings)

        drawn_count = 0
        benchmark_draw_token = benchmark_recorder.start_section("constellation.label_drawing", metadata={"labels": len(labels)}) if benchmark_recorder is not None else None
        for projected_label in labels:
            text = projected_label.label.name
            static_text, width, height, cache_hit = self._cached_static_text(text, font, settings.label_size_pt)
            if cache_hit:
                self.last_cache_hits += 1
            else:
                self.last_cache_misses += 1
            origin = QPointF(projected_label.x - width * 0.5, projected_label.y - height * 0.5)
            painter.setPen(QPen(shadow_color, 1.0))
            painter.drawStaticText(QPointF(origin.x() + 1.0, origin.y() + 1.0), static_text)
            painter.setPen(QPen(color, 1.0))
            painter.drawStaticText(origin, static_text)
            drawn_count += 1
        if benchmark_recorder is not None:
            benchmark_recorder.stop_section(
                benchmark_draw_token,
                metadata={"drawn_labels": drawn_count, "cache_hits": self.last_cache_hits, "cache_misses": self.last_cache_misses},
            )
        return drawn_count

    @staticmethod
    def _label_alpha(settings: ConstellationOverlaySettings, field_width_deg: float) -> float:
        base_alpha = max(0.0, min(1.0, float(settings.label_opacity)))
        full_width = max(1.0, float(settings.label_full_opacity_field_width_deg))
        hidden_width = max(full_width + 1.0, float(settings.label_hidden_field_width_deg))
        field_width = float(field_width_deg)
        if field_width >= hidden_width:
            return 0.0
        if field_width <= full_width:
            return base_alpha
        fade = (hidden_width - field_width) / (hidden_width - full_width)
        fade = fade * fade * (3.0 - 2.0 * fade)
        return base_alpha * fade

    @staticmethod
    def _label_font(settings: ConstellationOverlaySettings) -> QFont:
        font = QFont("Segoe UI")
        font.setPointSizeF(max(6.0, min(28.0, float(settings.label_size_pt))))
        font.setWeight(QFont.Weight.Medium)
        return font

    def _cached_static_text(self, text: str, font: QFont, label_size_pt: float) -> tuple[QStaticText, float, float, bool]:
        benchmark_recorder = get_benchmark_recorder() if BENCHMARK_ENABLED else None

        benchmark_lookup_token = benchmark_recorder.start_section("constellation.label_text_cache_lookup") if benchmark_recorder is not None else None
        cache_key = (text, round(float(label_size_pt), 2))
        cached = self._static_text_cache.get(cache_key)
        if cached is not None:
            self._static_text_cache.move_to_end(cache_key)
            if benchmark_recorder is not None:
                benchmark_recorder.stop_section(benchmark_lookup_token, metadata={"cache_status": "hit"})
            return (*cached, True)
        if benchmark_recorder is not None:
            benchmark_recorder.stop_section(benchmark_lookup_token, metadata={"cache_status": "miss"})
        benchmark_create_token = benchmark_recorder.start_section("constellation.label_text_cache_creation") if benchmark_recorder is not None else None
        static_text = QStaticText(text)
        static_text.setPerformanceHint(QStaticText.PerformanceHint.AggressiveCaching)
        static_text.prepare(QTransform(), font)
        text_size = static_text.size()
        cached_value = (static_text, float(text_size.width()), float(text_size.height()))
        self._static_text_cache[cache_key] = cached_value
        self._static_text_cache.move_to_end(cache_key)
        while len(self._static_text_cache) > self._cache_limit:
            self._static_text_cache.popitem(last=False)
        if benchmark_recorder is not None:
            benchmark_recorder.stop_section(benchmark_create_token, metadata={"cache_size": len(self._static_text_cache)})
        return (*cached_value, False)


class ConstellationOverlay:
    _CACHE_LIMIT = 16
    _PROJECTION_MARGIN = 1.12
    _LABEL_PROJECTION_MARGIN = 1.02
    _FRONT_HEMISPHERE_EPSILON = np.float32(1.0e-4)
    _MAX_PROJECTED_SEGMENT_DIAGONAL_FACTOR = 1.8

    def __init__(self, data_loader: ConstellationDataLoader | None = None) -> None:
        self._data_loader = data_loader or ConstellationDataLoader()
        self._line_renderer = ConstellationLineRenderer()
        self._label_renderer = ConstellationLabelRenderer()
        self._projected_cache: OrderedDict[tuple[object, ...], _ProjectedConstellationGeometry] = OrderedDict()
        self.last_metrics = ConstellationOverlayMetrics()

    @property
    def data_loader(self) -> ConstellationDataLoader:
        return self._data_loader

    def draw(self, painter: QPainter, scene: object, settings: ConstellationOverlaySettings) -> ConstellationOverlayMetrics:
        benchmark_recorder = get_benchmark_recorder() if BENCHMARK_ENABLED else None

        benchmark_token = benchmark_recorder.start_section("constellation.overlay_draw") if benchmark_recorder is not None else None
        start_seconds = perf_counter()
        if not settings.enabled or (not settings.show_lines and not settings.show_labels):
            self.last_metrics = ConstellationOverlayMetrics(cache_status="disabled", data_cache_status=self._data_loader.last_cache_status)
            if benchmark_recorder is not None:
                benchmark_recorder.stop_section(benchmark_token, metadata={"cache_status": "disabled"})
            return self.last_metrics

        try:
            data = self._data_loader.load()
        except Exception:
            self.last_metrics = ConstellationOverlayMetrics(cache_status="data-error", data_cache_status=self._data_loader.last_cache_status)
            if benchmark_recorder is not None:
                benchmark_recorder.stop_section(benchmark_token, metadata={"cache_status": "data-error"})
            return self.last_metrics

        geometry = self.projected_geometry(scene, data)
        drawn_line_count = 0
        drawn_label_count = 0
        painter.save()
        try:
            viewport_rect = getattr(scene, "viewport_rect", QRectF())
            painter.setClipRect(viewport_rect)
            painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
            if settings.show_lines:
                drawn_line_count = self._line_renderer.draw(painter, geometry.lines, settings)
            if settings.show_labels:
                drawn_label_count = self._label_renderer.draw(
                    painter,
                    geometry.labels,
                    settings,
                    float(getattr(scene, "field_width_deg", 180.0)),
                )
        finally:
            painter.restore()

        self.last_metrics = ConstellationOverlayMetrics(
            line_segment_count=len(data.line_segments),
            drawn_line_count=drawn_line_count,
            label_count=len(data.labels),
            drawn_label_count=drawn_label_count,
            projected_line_count=len(geometry.lines),
            projected_label_count=len(geometry.labels),
            label_text_cache_hits=self._label_renderer.last_cache_hits,
            label_text_cache_misses=self._label_renderer.last_cache_misses,
            overlay_seconds=perf_counter() - start_seconds,
            cache_status=geometry.cache_status,
            data_cache_status=self._data_loader.last_cache_status,
        )
        if benchmark_recorder is not None:
            benchmark_recorder.stop_section(
                benchmark_token,
                metadata={
                    "cache_status": geometry.cache_status,
                    "data_cache_status": self._data_loader.last_cache_status,
                    "line_segments": len(data.line_segments),
                    "drawn_lines": drawn_line_count,
                    "labels": len(data.labels),
                    "drawn_labels": drawn_label_count,
                    "projected_lines": len(geometry.lines),
                    "projected_labels": len(geometry.labels),
                    "label_text_cache_hits": self._label_renderer.last_cache_hits,
                    "label_text_cache_misses": self._label_renderer.last_cache_misses,
                },
            )
        return self.last_metrics

    def projected_geometry(self, scene: object, data: ConstellationOverlayData | None = None) -> _ProjectedConstellationGeometry:
        benchmark_recorder = get_benchmark_recorder() if BENCHMARK_ENABLED else None

        benchmark_token = benchmark_recorder.start_section("constellation.projected_geometry") if benchmark_recorder is not None else None
        resolved_data = self._data_loader.load() if data is None else data
        benchmark_cache_token = benchmark_recorder.start_section("constellation.projected_geometry_cache_lookup") if benchmark_recorder is not None else None
        cache_key = self._projected_cache_key(scene, resolved_data.source_key)
        cached_geometry = self._projected_cache.get(cache_key)
        if cached_geometry is not None:
            self._projected_cache.move_to_end(cache_key)
            if benchmark_recorder is not None:
                benchmark_recorder.stop_section(benchmark_cache_token, metadata={"cache_status": "hit"})
                benchmark_recorder.stop_section(benchmark_token, metadata={"cache_status": "hit"})
            return _ProjectedConstellationGeometry(
                cached_geometry.lines,
                cached_geometry.labels,
                "hit",
                cached_geometry.segments_considered,
                cached_geometry.labels_considered,
            )

        if benchmark_recorder is not None:
            benchmark_recorder.stop_section(benchmark_cache_token, metadata={"cache_status": "miss"})

        viewport_rect = getattr(scene, "viewport_rect", QRectF())
        benchmark_rebuild_token = benchmark_recorder.start_section("constellation.projected_geometry_cache_rebuild") if benchmark_recorder is not None else None
        projected_lines = self._project_lines(scene, viewport_rect, resolved_data.line_unit_vectors)
        projected_labels = self._project_labels(scene, viewport_rect, resolved_data.labels, resolved_data.label_unit_vectors)
        geometry = _ProjectedConstellationGeometry(
            projected_lines,
            projected_labels,
            "miss",
            int(resolved_data.line_unit_vectors.shape[0]) if resolved_data.line_unit_vectors.ndim >= 1 else 0,
            len(resolved_data.labels),
        )
        self._projected_cache[cache_key] = geometry
        self._projected_cache.move_to_end(cache_key)
        while len(self._projected_cache) > self._CACHE_LIMIT:
            self._projected_cache.popitem(last=False)
        if benchmark_recorder is not None:
            benchmark_recorder.stop_section(
                benchmark_rebuild_token,
                metadata={"projected_lines": len(projected_lines), "projected_labels": len(projected_labels)},
            )
            benchmark_recorder.stop_section(
                benchmark_token,
                metadata={"cache_status": "miss", "projected_lines": len(projected_lines), "projected_labels": len(projected_labels)},
            )
        return geometry

    def clear_projected_cache(self) -> None:
        self._projected_cache.clear()

    def _project_lines(self, scene: object, rect: QRectF, line_vectors: np.ndarray) -> tuple[QLineF, ...]:
        benchmark_recorder = get_benchmark_recorder() if BENCHMARK_ENABLED else None

        benchmark_token = benchmark_recorder.start_section("constellation.project_lines", metadata={"segments": int(line_vectors.shape[0]) if line_vectors.ndim >= 1 else 0}) if benchmark_recorder is not None else None
        if line_vectors.size == 0 or rect.width() <= 1.0 or rect.height() <= 1.0:
            if benchmark_recorder is not None:
                benchmark_recorder.stop_section(benchmark_token, metadata={"projected_lines": 0})
            return ()

        benchmark_visibility_token = benchmark_recorder.start_section("constellation.line_visibility_culling", metadata={"segments": int(line_vectors.shape[0])}) if benchmark_recorder is not None else None
        forward, up, right = self._projection_axes(scene)
        horizon_pairs = self._horizon_vectors_from_equatorial(scene, line_vectors.reshape((-1, 3))).reshape((-1, 2, 3))
        front_dot_pairs = np.clip(horizon_pairs @ forward, np.float32(-1.0), np.float32(1.0))
        visible_pairs = front_dot_pairs > self._FRONT_HEMISPHERE_EPSILON
        keep_mask = np.any(visible_pairs, axis=1)
        if benchmark_recorder is not None:
            benchmark_recorder.stop_section(
                benchmark_visibility_token,
                metadata={"kept_segments": int(np.count_nonzero(keep_mask)), "segments": int(line_vectors.shape[0])},
            )
        if not np.any(keep_mask):
            if benchmark_recorder is not None:
                benchmark_recorder.stop_section(benchmark_token, metadata={"projected_lines": 0})
            return ()

        benchmark_clip_token = benchmark_recorder.start_section("constellation.line_clipping", metadata={"kept_segments": int(np.count_nonzero(keep_mask))}) if benchmark_recorder is not None else None
        clipped_pairs = horizon_pairs[keep_mask].copy()
        clipped_visible_pairs = visible_pairs[keep_mask]
        clipped_front_dot_pairs = front_dot_pairs[keep_mask]
        crossing_mask = clipped_visible_pairs[:, 0] != clipped_visible_pairs[:, 1]
        if np.any(crossing_mask):
            crossing_indices = np.nonzero(crossing_mask)[0]
            da = clipped_front_dot_pairs[crossing_indices, 0]
            db = clipped_front_dot_pairs[crossing_indices, 1]
            denominator = db - da
            safe_crossing_mask = np.abs(denominator) > np.float32(1.0e-9)
            if np.any(safe_crossing_mask):
                crossing_indices = crossing_indices[safe_crossing_mask]
                start_vectors = clipped_pairs[crossing_indices, 0, :]
                end_vectors = clipped_pairs[crossing_indices, 1, :]
                da = da[safe_crossing_mask]
                db = db[safe_crossing_mask]
                interpolation = np.clip(
                    (self._FRONT_HEMISPHERE_EPSILON - da) / (db - da),
                    np.float32(0.0),
                    np.float32(1.0),
                ).astype(np.float32, copy=False)
                clipped_vectors = start_vectors + (end_vectors - start_vectors) * interpolation[:, np.newaxis]
                clipped_norms = np.linalg.norm(clipped_vectors, axis=1, keepdims=True)
                clipped_norms = np.where(clipped_norms > np.float32(1.0e-9), clipped_norms, np.float32(1.0))
                clipped_vectors = clipped_vectors / clipped_norms
                replace_start_mask = ~clipped_visible_pairs[crossing_indices, 0]
                if np.any(replace_start_mask):
                    clipped_pairs[crossing_indices[replace_start_mask], 0, :] = clipped_vectors[replace_start_mask]
                if np.any(~replace_start_mask):
                    clipped_pairs[crossing_indices[~replace_start_mask], 1, :] = clipped_vectors[~replace_start_mask]

        if benchmark_recorder is not None:
            benchmark_recorder.stop_section(
                benchmark_clip_token,
                metadata={"clipped_segments": int(clipped_pairs.shape[0]), "crossing_segments": int(np.count_nonzero(crossing_mask))},
            )

        benchmark_projection_token = benchmark_recorder.start_section("constellation.line_projection", metadata={"vertices": int(clipped_pairs.reshape((-1, 3)).shape[0])}) if benchmark_recorder is not None else None
        x_values, y_values, valid = self._project_horizon_vectors(
            scene,
            rect,
            clipped_pairs.reshape((-1, 3)),
            forward,
            up,
            right,
            self._PROJECTION_MARGIN,
        )
        if benchmark_recorder is not None:
            benchmark_recorder.stop_section(benchmark_projection_token, metadata={"valid_vertices": int(np.count_nonzero(valid))})
        x_pairs = x_values.reshape((-1, 2))
        y_pairs = y_values.reshape((-1, 2))
        valid_pairs = valid.reshape((-1, 2))
        expanded_rect = rect.adjusted(-8.0, -8.0, 8.0, 8.0)
        viewport_diagonal = math.hypot(rect.width(), rect.height())
        max_segment_length = max(24.0, viewport_diagonal * self._MAX_PROJECTED_SEGMENT_DIAGONAL_FACTOR)
        absurd_margin = max(64.0, viewport_diagonal)
        absurd_rect = rect.adjusted(-absurd_margin, -absurd_margin, absurd_margin, absurd_margin)
        lines: list[QLineF] = []
        left = expanded_rect.left()
        right = expanded_rect.right()
        top = expanded_rect.top()
        bottom = expanded_rect.bottom()
        absurd_left = absurd_rect.left()
        absurd_right = absurd_rect.right()
        absurd_top = absurd_rect.top()
        absurd_bottom = absurd_rect.bottom()
        benchmark_post_clip_token = benchmark_recorder.start_section("constellation.line_post_projection_culling", metadata={"projected_segments": int(x_pairs.shape[0])}) if benchmark_recorder is not None else None
        for index in range(x_pairs.shape[0]):
            start_x = float(x_pairs[index, 0])
            start_y = float(y_pairs[index, 0])
            end_x = float(x_pairs[index, 1])
            end_y = float(y_pairs[index, 1])
            if not (math.isfinite(start_x) and math.isfinite(start_y) and math.isfinite(end_x) and math.isfinite(end_y)):
                continue
            if (
                start_x < absurd_left
                or start_x > absurd_right
                or start_y < absurd_top
                or start_y > absurd_bottom
                or end_x < absurd_left
                or end_x > absurd_right
                or end_y < absurd_top
                or end_y > absurd_bottom
            ):
                continue
            if not (bool(valid_pairs[index, 0]) or bool(valid_pairs[index, 1])):
                if max(start_x, end_x) < left or min(start_x, end_x) > right or max(start_y, end_y) < top or min(start_y, end_y) > bottom:
                    continue
            segment_length = math.hypot(end_x - start_x, end_y - start_y)
            if not math.isfinite(segment_length) or segment_length <= 1.0e-6 or segment_length > max_segment_length:
                continue
            lines.append(QLineF(start_x, start_y, end_x, end_y))
        if benchmark_recorder is not None:
            benchmark_recorder.stop_section(benchmark_post_clip_token, metadata={"lines": len(lines)})
        if benchmark_recorder is not None:
            benchmark_recorder.stop_section(benchmark_token, metadata={"projected_lines": len(lines)})
        return tuple(lines)

    def _project_labels(
        self,
        scene: object,
        rect: QRectF,
        labels: tuple[ConstellationLabel, ...],
        label_vectors: np.ndarray,
    ) -> tuple[_ProjectedConstellationLabel, ...]:
        benchmark_recorder = get_benchmark_recorder() if BENCHMARK_ENABLED else None

        benchmark_token = benchmark_recorder.start_section("constellation.project_labels", metadata={"labels": len(labels)}) if benchmark_recorder is not None else None
        if label_vectors.size == 0 or rect.width() <= 1.0 or rect.height() <= 1.0:
            if benchmark_recorder is not None:
                benchmark_recorder.stop_section(benchmark_token, metadata={"projected_labels": 0})
            return ()

        benchmark_selection_token = benchmark_recorder.start_section("constellation.label_candidate_selection", metadata={"labels": len(labels)}) if benchmark_recorder is not None else None
        forward, up, right = self._projection_axes(scene)
        horizon_vectors = self._horizon_vectors_from_equatorial(scene, label_vectors)
        front_dot = np.clip(horizon_vectors @ forward, np.float32(-1.0), np.float32(1.0))
        visible_indices = np.nonzero(front_dot > self._FRONT_HEMISPHERE_EPSILON)[0]
        if benchmark_recorder is not None:
            benchmark_recorder.stop_section(benchmark_selection_token, metadata={"visible_candidates": int(visible_indices.size)})
        if visible_indices.size == 0:
            if benchmark_recorder is not None:
                benchmark_recorder.stop_section(benchmark_token, metadata={"projected_labels": 0})
            return ()

        benchmark_projection_token = benchmark_recorder.start_section("constellation.label_projection", metadata={"labels": int(visible_indices.size)}) if benchmark_recorder is not None else None
        x_values, y_values, valid = self._project_horizon_vectors(
            scene,
            rect,
            horizon_vectors[visible_indices],
            forward,
            up,
            right,
            self._LABEL_PROJECTION_MARGIN,
        )
        if benchmark_recorder is not None:
            benchmark_recorder.stop_section(benchmark_projection_token, metadata={"valid_labels": int(np.count_nonzero(valid))})
        projected_labels: list[_ProjectedConstellationLabel] = []
        viewport_diagonal = math.hypot(rect.width(), rect.height())
        label_margin = max(12.0, min(64.0, viewport_diagonal * 0.08))
        label_rect = rect.adjusted(-label_margin, -label_margin, label_margin, label_margin)
        label_left = label_rect.left()
        label_right = label_rect.right()
        label_top = label_rect.top()
        label_bottom = label_rect.bottom()
        benchmark_visibility_token = benchmark_recorder.start_section("constellation.label_collision_visibility_fade", metadata={"labels": int(visible_indices.size)}) if benchmark_recorder is not None else None
        for local_index, label_index in enumerate(visible_indices):
            if not bool(valid[local_index]):
                continue
            x_value = float(x_values[local_index])
            y_value = float(y_values[local_index])
            if not math.isfinite(x_value) or not math.isfinite(y_value):
                continue
            if x_value < label_left or x_value > label_right or y_value < label_top or y_value > label_bottom:
                continue
            projected_labels.append(_ProjectedConstellationLabel(label=labels[int(label_index)], x=x_value, y=y_value))
        if benchmark_recorder is not None:
            benchmark_recorder.stop_section(benchmark_visibility_token, metadata={"projected_labels": len(projected_labels)})
        if benchmark_recorder is not None:
            benchmark_recorder.stop_section(benchmark_token, metadata={"projected_labels": len(projected_labels)})
        return tuple(projected_labels)

    @classmethod
    def _project_unit_vectors(
        cls,
        scene: object,
        rect: QRectF,
        equatorial_vectors: np.ndarray,
        normalized_margin: float,
    ) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
        if equatorial_vectors.size == 0:
            empty = np.empty((0,), dtype=np.float32)
            return empty, empty, np.empty((0,), dtype=bool)

        horizon_vectors = cls._horizon_vectors_from_equatorial(scene, equatorial_vectors)
        forward, up, right = cls._projection_axes(scene)
        return cls._project_horizon_vectors(scene, rect, horizon_vectors, forward, up, right, normalized_margin)

    @staticmethod
    def _horizon_vectors_from_equatorial(scene: object, equatorial_vectors: np.ndarray) -> np.ndarray:
        matrix = np.asarray(
            getattr(scene, "equatorial_to_horizon_matrix", ((1.0, 0.0, 0.0), (0.0, 1.0, 0.0), (0.0, 0.0, 1.0))),
            dtype=np.float32,
        )
        horizon_vectors = np.asarray(equatorial_vectors, dtype=np.float32) @ matrix.T
        norms = np.linalg.norm(horizon_vectors, axis=1)
        norms = np.where(norms > np.float32(1.0e-9), norms, np.float32(1.0))
        return horizon_vectors / norms[:, np.newaxis]

    @classmethod
    def _projection_axes(cls, scene: object) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
        forward = cls._normalized_np_vector(getattr(scene, "camera_forward", (0.0, 1.0, 0.0)))
        up = cls._orthonormalized_up_np(forward, getattr(scene, "camera_up", (0.0, 0.0, 1.0)))
        right = np.cross(forward, up)
        right_norm = np.linalg.norm(right)
        if right_norm <= np.float32(1.0e-9):
            right = np.asarray((1.0, 0.0, 0.0), dtype=np.float32)
        else:
            right = right / right_norm
        return forward, up, right

    @staticmethod
    def _project_horizon_vectors(
        scene: object,
        rect: QRectF,
        horizon_vectors: np.ndarray,
        forward: np.ndarray,
        up: np.ndarray,
        right: np.ndarray,
        normalized_margin: float,
    ) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
        if horizon_vectors.size == 0:
            empty = np.empty((0,), dtype=np.float32)
            return empty, empty, np.empty((0,), dtype=bool)

        half_width_rad = np.float32(math.radians(max(1.0e-6, float(getattr(scene, "field_width_deg", 180.0))) / 2.0))
        half_height_rad = np.float32(math.radians(max(1.0e-6, float(getattr(scene, "field_height_deg", 90.0))) / 2.0))
        center_x = np.float32(rect.center().x())
        center_y = np.float32(rect.center().y())
        half_rect_width = np.float32(rect.width() / 2.0)
        half_rect_height = np.float32(rect.height() / 2.0)

        cos_distance = np.clip(horizon_vectors @ forward, np.float32(-1.0), np.float32(1.0))
        angular_distance = np.arccos(cos_distance)
        sin_distance = np.sin(angular_distance)
        scale = np.where(np.abs(sin_distance) > np.float32(1.0e-6), angular_distance / sin_distance, np.float32(1.0))
        x_rad = scale * (horizon_vectors @ right)
        y_rad = scale * (horizon_vectors @ up)
        x_normalized = x_rad / half_width_rad
        y_normalized = y_rad / half_height_rad
        x_values = center_x + x_normalized * half_rect_width
        y_values = center_y - y_normalized * half_rect_height
        finite = np.isfinite(x_values) & np.isfinite(y_values)
        valid = finite & (np.abs(x_normalized) <= np.float32(normalized_margin)) & (np.abs(y_normalized) <= np.float32(normalized_margin))
        return x_values.astype(np.float32, copy=False), y_values.astype(np.float32, copy=False), valid

    @staticmethod
    def _normalized_np_vector(value: object) -> np.ndarray:
        vector = np.asarray(value, dtype=np.float32).reshape((3,))
        norm = np.linalg.norm(vector)
        if norm <= np.float32(1.0e-9):
            return np.asarray((0.0, 1.0, 0.0), dtype=np.float32)
        return vector / norm

    @staticmethod
    def _orthonormalized_up_np(forward: np.ndarray, up_value: object) -> np.ndarray:
        up = ConstellationOverlay._normalized_np_vector(up_value)
        projected = up - forward * np.dot(up, forward)
        norm = np.linalg.norm(projected)
        if norm > np.float32(1.0e-6):
            return projected / norm
        world_up = np.asarray((0.0, 0.0, 1.0), dtype=np.float32)
        projected = world_up - forward * np.dot(world_up, forward)
        norm = np.linalg.norm(projected)
        if norm > np.float32(1.0e-6):
            return projected / norm
        return np.asarray((0.0, 1.0, 0.0), dtype=np.float32)

    @staticmethod
    def _projected_cache_key(scene: object, source_key: tuple[object, ...]) -> tuple[object, ...]:
        rect = getattr(scene, "viewport_rect", QRectF())
        return (
            "constellation-overlay-v1",
            source_key,
            round(float(rect.left()), 3),
            round(float(rect.top()), 3),
            round(float(rect.width()), 3),
            round(float(rect.height()), 3),
            round(float(getattr(scene, "device_pixel_ratio", 1.0)), 3),
            round(float(getattr(scene, "field_width_deg", 180.0)), 6),
            round(float(getattr(scene, "field_height_deg", 90.0)), 6),
            *(round(float(component), 6) for component in getattr(scene, "camera_forward", (0.0, 1.0, 0.0))),
            *(round(float(component), 6) for component in getattr(scene, "camera_up", (0.0, 0.0, 1.0))),
            *(round(float(component), 6) for row in getattr(scene, "equatorial_to_horizon_matrix", ((1.0, 0.0, 0.0), (0.0, 1.0, 0.0), (0.0, 0.0, 1.0))) for component in row),
        )
