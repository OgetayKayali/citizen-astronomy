"""GPU-instanced overlay labels and marker circles for Sky Atlas.

Whole strings are rasterized into a CPU font atlas and drawn as one quad each.
Object circles use a separate instanced ring shader. QPainter remains the fallback.
"""

from __future__ import annotations

import logging
import math
from dataclasses import dataclass
from time import perf_counter
from typing import Sequence

import numpy as np
from PySide6.QtCore import QPointF, QRect, QRectF
from PySide6.QtGui import QColor, QFont, QFontMetricsF, QImage, QPainter
from PySide6.QtOpenGL import (
    QOpenGLBuffer,
    QOpenGLFunctions_2_0,
    QOpenGLShader,
    QOpenGLShaderProgram,
)

_LOGGER = logging.getLogger(__name__)

_LABEL_INSTANCE_FLOATS = 12
_LABEL_INSTANCE_STRIDE = _LABEL_INSTANCE_FLOATS * 4
_MARKER_INSTANCE_FLOATS = 12
_MARKER_INSTANCE_STRIDE = _MARKER_INSTANCE_FLOATS * 4
_EMPTY_LABELS = np.zeros((0, _LABEL_INSTANCE_FLOATS), dtype=np.float32)
_EMPTY_MARKERS = np.zeros((0, _MARKER_INSTANCE_FLOATS), dtype=np.float32)

_GL_BLEND = 0x0BE2
_GL_BLEND_SRC = 0x0BE1
_GL_BLEND_DST = 0x0BE0
_GL_BLEND_EQUATION = 0x8009
_GL_FUNC_ADD = 0x8006
_GL_SRC_ALPHA = 0x0302
_GL_ONE_MINUS_SRC_ALPHA = 0x0303
_GL_DEPTH_TEST = 0x0B71
_GL_DEPTH_WRITEMASK = 0x0B72
_GL_CULL_FACE = 0x0B44
_GL_SCISSOR_TEST = 0x0C11
_GL_FRAMEBUFFER_SRGB = 0x8DB9
_GL_CURRENT_PROGRAM = 0x8B8D
_GL_ACTIVE_TEXTURE = 0x84E0
_GL_TEXTURE0 = 0x84C0
_GL_TEXTURE_2D = 0x0DE1
_GL_TEXTURE_BINDING_2D = 0x8069
_GL_ARRAY_BUFFER = 0x8892
_GL_ARRAY_BUFFER_BINDING = 0x8894
_GL_FLOAT = 0x1406
_GL_TRIANGLES = 0x0004
_GL_UNPACK_ALIGNMENT = 0x0CF5
_GL_UNPACK_ROW_LENGTH = 0x0CF2
_GL_UNPACK_SKIP_ROWS = 0x0CF3
_GL_UNPACK_SKIP_PIXELS = 0x0CF4
_GL_RGBA = 0x1908
_GL_RGBA8 = 0x8058
_GL_UNSIGNED_BYTE = 0x1401
_GL_FALSE = 0
_GL_LINEAR = 0x2601
_GL_CLAMP_TO_EDGE = 0x812F
_GL_TEXTURE_MIN_FILTER = 0x2801
_GL_TEXTURE_MAG_FILTER = 0x2800
_GL_TEXTURE_WRAP_S = 0x2802
_GL_TEXTURE_WRAP_T = 0x2803

_ATLAS_WIDTH = 2048
_ATLAS_HEIGHT = 2048
_SPRITE_PAD = 2
_SPRITE_GAP = 2
_SHADOW_OFFSET_PX = 1.0


@dataclass(frozen=True, slots=True)
class GpuLabelDraw:
    """One screen-space label. Origin is the QPainter.drawText baseline unless noted."""

    font: QFont
    origin_x: float
    origin_y: float
    text: str
    color: QColor
    shadow_color: QColor | None = None
    baseline: bool = True


@dataclass(frozen=True, slots=True)
class GpuMarkerDraw:
    """Screen-space circle: filled disc plus a stroked rim."""

    x: float
    y: float
    radius: float
    fill: QColor
    edge: QColor
    stroke: float = 1.4


@dataclass(slots=True)
class LabelRendererMetrics:
    available: bool = False
    backend: str = "cpu"
    label_count: int = 0
    glyph_count: int = 0
    instance_count: int = 0
    marker_count: int = 0
    atlas_glyph_count: int = 0
    upload_seconds: float = 0.0
    draw_seconds: float = 0.0
    fallback_reason: str = ""


@dataclass(frozen=True, slots=True)
class _Sprite:
    x: int
    y: int
    width: int
    height: int
    pad: int
    ascent: float


def font_atlas_key(font: QFont) -> tuple[object, ...]:
    families = tuple(str(family) for family in font.families() if str(family).strip())
    if not families:
        family = str(font.family() or "").strip()
        families = (family,) if family else ("",)
    return (
        families,
        round(float(font.pointSizeF()), 2),
        int(font.weight()),
        bool(font.italic()),
        bool(font.bold()),
    )


def _color_rgba(color: QColor) -> tuple[float, float, float, float]:
    return (
        max(0.0, min(1.0, float(color.redF()))),
        max(0.0, min(1.0, float(color.greenF()))),
        max(0.0, min(1.0, float(color.blueF()))),
        max(0.0, min(1.0, float(color.alphaF()))),
    )


class FontAtlas:
    """CPU-packed whole-string sprites, uploaded as one GL texture."""

    def __init__(self, width: int = _ATLAS_WIDTH, height: int = _ATLAS_HEIGHT) -> None:
        self.width = int(width)
        self.height = int(height)
        self._sprites: dict[tuple[object, ...], _Sprite] = {}
        self._cursor_x = _SPRITE_GAP
        self._cursor_y = _SPRITE_GAP
        self._shelf_height = 0
        self._full = False
        self._dirty = True
        self.image = QImage(self.width, self.height, QImage.Format.Format_RGBA8888)
        self.image.fill(0)

    @property
    def glyph_count(self) -> int:
        return len(self._sprites)

    @property
    def is_full(self) -> bool:
        return self._full

    def metrics(self, font: QFont) -> QFontMetricsF:
        return QFontMetricsF(font)

    def measure_text(self, font: QFont, text: str) -> tuple[float, float, float]:
        metrics = self.metrics(font)
        return (
            float(metrics.horizontalAdvance(text)),
            float(metrics.ascent()),
            float(metrics.height()),
        )

    def sprite(self, font: QFont, text: str) -> _Sprite | None:
        resolved = str(text or "")
        if not resolved:
            return None
        key = (*font_atlas_key(font), resolved)
        cached = self._sprites.get(key)
        if cached is not None:
            return cached
        packed = self._rasterize_and_pack(font, resolved)
        if packed is None:
            return None
        self._sprites[key] = packed
        return packed

    def reset(self) -> None:
        self._sprites.clear()
        self._cursor_x = _SPRITE_GAP
        self._cursor_y = _SPRITE_GAP
        self._shelf_height = 0
        self._full = False
        self._dirty = True
        self.image.fill(0)

    def take_dirty(self) -> bool:
        dirty = self._dirty
        self._dirty = False
        return dirty

    def _rasterize_and_pack(self, font: QFont, text: str) -> _Sprite | None:
        metrics = self.metrics(font)
        text_width = float(metrics.horizontalAdvance(text))
        ascent = float(metrics.ascent())
        text_height = float(metrics.height())
        if text_width <= 0.05 or text_height <= 0.05:
            return None
        pixel_width = max(1, int(math.ceil(text_width)) + _SPRITE_PAD * 2)
        pixel_height = max(1, int(math.ceil(text_height)) + _SPRITE_PAD * 2)
        placed = self._allocate(pixel_width, pixel_height)
        if placed is None:
            self._full = True
            return None
        atlas_x, atlas_y = placed
        painter = QPainter(self.image)
        try:
            painter.setCompositionMode(QPainter.CompositionMode.CompositionMode_Source)
            painter.fillRect(QRect(atlas_x, atlas_y, pixel_width, pixel_height), QColor(0, 0, 0, 0))
            painter.setCompositionMode(QPainter.CompositionMode.CompositionMode_SourceOver)
            painter.setRenderHint(QPainter.RenderHint.TextAntialiasing, True)
            painter.setFont(font)
            painter.setPen(QColor(255, 255, 255, 255))
            painter.drawText(
                QPointF(atlas_x + _SPRITE_PAD, atlas_y + _SPRITE_PAD + ascent),
                text,
            )
        finally:
            painter.end()
        self._dirty = True
        return _Sprite(atlas_x, atlas_y, pixel_width, pixel_height, _SPRITE_PAD, ascent)

    def _allocate(self, width: int, height: int) -> tuple[int, int] | None:
        if width <= 0 or height <= 0:
            return (0, 0)
        if self._cursor_x + width + _SPRITE_GAP > self.width:
            self._cursor_y += self._shelf_height + _SPRITE_GAP
            self._cursor_x = _SPRITE_GAP
            self._shelf_height = 0
        if self._cursor_y + height + _SPRITE_GAP > self.height:
            return None
        placed = (self._cursor_x, self._cursor_y)
        self._cursor_x += width + _SPRITE_GAP
        self._shelf_height = max(self._shelf_height, height)
        return placed


def _qimage_rgba(image: QImage) -> np.ndarray:
    converted = image.convertToFormat(QImage.Format.Format_RGBA8888)
    height = int(converted.height())
    width = int(converted.width())
    stride = int(converted.bytesPerLine())
    pointer = converted.constBits()
    try:
        raw = np.frombuffer(pointer, dtype=np.uint8, count=stride * height).reshape(height, stride)
    except TypeError:
        raw = np.frombuffer(bytes(pointer), dtype=np.uint8, count=stride * height).reshape(height, stride)
    return np.ascontiguousarray(raw[:, : width * 4].reshape(height, width, 4))


class SkyViewLabelRenderer:
    """Owns the label atlas, circle shader, and instanced overlay buffers."""

    _LABEL_VERTEX_SHADER = """
#version 120
attribute vec2 a_corner;
attribute vec2 a_origin;
attribute vec2 a_size;
attribute vec2 a_uv0;
attribute vec2 a_uv1;
attribute vec4 a_color;

uniform vec4 u_viewport_rect;

varying vec2 v_uv;
varying vec4 v_color;

void main() {
    vec2 logical_pos = a_origin + (a_corner * a_size);
    float safe_w = max(u_viewport_rect.z, 1.0e-6);
    float safe_h = max(u_viewport_rect.w, 1.0e-6);
    float clip_x = (((logical_pos.x - u_viewport_rect.x) / safe_w) * 2.0) - 1.0;
    float clip_y = 1.0 - (((logical_pos.y - u_viewport_rect.y) / safe_h) * 2.0);
    gl_Position = vec4(clip_x, clip_y, 0.0, 1.0);
    v_uv = mix(a_uv0, a_uv1, a_corner);
    v_color = a_color;
}
"""

    _LABEL_FRAGMENT_SHADER = """
#version 120
uniform sampler2D u_atlas;

varying vec2 v_uv;
varying vec4 v_color;

void main() {
    float alpha = texture2D(u_atlas, v_uv).a * v_color.a;
    if (alpha <= 0.004) {
        discard;
    }
    gl_FragColor = vec4(v_color.rgb, alpha);
}
"""

    _MARKER_VERTEX_SHADER = """
#version 120
attribute vec2 a_corner;
attribute vec2 a_center;
attribute float a_radius;
attribute float a_stroke;
attribute vec4 a_fill;
attribute vec4 a_edge;

uniform vec4 u_viewport_rect;

varying vec2 v_offset_px;
varying float v_radius;
varying float v_stroke;
varying vec4 v_fill;
varying vec4 v_edge;

void main() {
    float pad = max(a_stroke, 1.0) + 1.75;
    float extent = max(a_radius, 0.5) + pad;
    vec2 logical_pos = a_center + (a_corner * extent);
    float safe_w = max(u_viewport_rect.z, 1.0e-6);
    float safe_h = max(u_viewport_rect.w, 1.0e-6);
    float clip_x = (((logical_pos.x - u_viewport_rect.x) / safe_w) * 2.0) - 1.0;
    float clip_y = 1.0 - (((logical_pos.y - u_viewport_rect.y) / safe_h) * 2.0);
    gl_Position = vec4(clip_x, clip_y, 0.0, 1.0);
    v_offset_px = a_corner * extent;
    v_radius = a_radius;
    v_stroke = a_stroke;
    v_fill = a_fill;
    v_edge = a_edge;
}
"""

    _MARKER_FRAGMENT_SHADER = """
#version 120
varying vec2 v_offset_px;
varying float v_radius;
varying float v_stroke;
varying vec4 v_fill;
varying vec4 v_edge;

void main() {
    float dist = length(v_offset_px);
    float aa = 1.15;
    float outer = max(v_radius, 0.5);
    float inner = max(0.0, outer - max(v_stroke, 0.75));
    float fill = 1.0 - smoothstep(inner - aa, inner + aa, dist);
    float ring = smoothstep(inner - aa, inner + aa, dist) * (1.0 - smoothstep(outer - aa, outer + aa, dist));
    vec4 color = (v_fill * fill) + (v_edge * ring);
    if (color.a <= 0.004) {
        discard;
    }
    gl_FragColor = color;
}
"""

    # Keep the previous public names so shader tests and docs can find the label path.
    _VERTEX_SHADER = _LABEL_VERTEX_SHADER
    _FRAGMENT_SHADER = _LABEL_FRAGMENT_SHADER

    def __init__(self) -> None:
        self.atlas = FontAtlas()
        self.metrics = LabelRendererMetrics()
        self._available = False
        self._disabled_reason = ""
        self._functions: QOpenGLFunctions_2_0 | None = None
        self._gl = None
        self._label_program: QOpenGLShaderProgram | None = None
        self._marker_program: QOpenGLShaderProgram | None = None
        self._label_locations: dict[str, int] = {}
        self._marker_locations: dict[str, int] = {}
        self._label_quad_buffer: QOpenGLBuffer | None = None
        self._marker_quad_buffer: QOpenGLBuffer | None = None
        self._label_instance_buffer: QOpenGLBuffer | None = None
        self._marker_instance_buffer: QOpenGLBuffer | None = None
        self._label_instance_capacity = 0
        self._marker_instance_capacity = 0
        self._texture_id = 0
        self._gl_vao_id = 0
        self._use_instancing = False

    @property
    def available(self) -> bool:
        return self._available

    @property
    def disabled_reason(self) -> str:
        return self._disabled_reason

    def initialize(self, functions: QOpenGLFunctions_2_0 | None) -> bool:
        if functions is None:
            self._available = False
            self._disabled_reason = "OpenGL functions unavailable"
            _LOGGER.warning("SkyViewLabelRenderer initialize failed: %s", self._disabled_reason)
            return False
        if self._available and self._label_program is not None:
            return True
        self._functions = functions
        try:
            self._gl = None
            self._label_program = self._compile_program(self._LABEL_VERTEX_SHADER, self._LABEL_FRAGMENT_SHADER, "label")
            self._marker_program = self._compile_program(self._MARKER_VERTEX_SHADER, self._MARKER_FRAGMENT_SHADER, "marker")
            self._label_locations = self._resolve_label_locations(self._label_program)
            self._marker_locations = self._resolve_marker_locations(self._marker_program)
            self._label_quad_buffer = self._create_quad_buffer(((0.0, 0.0), (1.0, 0.0), (0.0, 1.0), (0.0, 1.0), (1.0, 0.0), (1.0, 1.0)))
            self._marker_quad_buffer = self._create_quad_buffer(((-1.0, -1.0), (1.0, -1.0), (-1.0, 1.0), (-1.0, 1.0), (1.0, -1.0), (1.0, 1.0)))
            self._label_instance_buffer = self._create_dynamic_buffer("label")
            self._marker_instance_buffer = self._create_dynamic_buffer("marker")
            self._gl = self._import_opengl()
            if not self._probe_instancing():
                raise RuntimeError("required OpenGL instancing or VAO functions are unavailable")
            self._use_instancing = True
            self._create_atlas_texture()
            self._restore_qt_unpack(self._gl)
            self._ensure_vao_bound()
            self._release_vao_binding()
            self._available = True
            self._disabled_reason = ""
            _LOGGER.info(
                "SkyViewLabelRenderer backend=instanced atlas=%sx%s vao=%s",
                self.atlas.width,
                self.atlas.height,
                self._gl_vao_id,
            )
            return True
        except Exception as exc:
            reason = f"shader/resource init failed: {exc}"
            self.release()
            self._available = False
            self._disabled_reason = reason
            _LOGGER.warning("SkyViewLabelRenderer initialize failed: %s", reason)
            return False

    def release(self) -> None:
        self._available = False
        self._use_instancing = False
        if self._gl_vao_id and self._gl is not None:
            try:
                self._gl.glDeleteVertexArrays(1, [int(self._gl_vao_id)])
            except Exception:
                pass
            self._gl_vao_id = 0
        if self._texture_id and self._gl is not None:
            try:
                self._gl.glDeleteTextures(1, [int(self._texture_id)])
            except Exception:
                pass
            self._texture_id = 0
        for program in (self._label_program, self._marker_program):
            if program is None:
                continue
            try:
                program.release()
            except Exception:
                pass
            try:
                program.removeAllShaders()
            except Exception:
                pass
        self._label_program = None
        self._marker_program = None
        for buffer in (
            self._label_quad_buffer,
            self._marker_quad_buffer,
            self._label_instance_buffer,
            self._marker_instance_buffer,
        ):
            if buffer is None:
                continue
            try:
                buffer.destroy()
            except Exception:
                pass
        self._label_quad_buffer = None
        self._marker_quad_buffer = None
        self._label_instance_buffer = None
        self._marker_instance_buffer = None
        self._label_instance_capacity = 0
        self._marker_instance_capacity = 0
        self._functions = None
        self._gl = None
        self._label_locations = {}
        self._marker_locations = {}

    def pack_instances(self, labels: Sequence[GpuLabelDraw]) -> np.ndarray:
        if not labels:
            return _EMPTY_LABELS.copy()
        rows = self._collect_label_rows(labels)
        if not rows and self.atlas.is_full:
            self.atlas.reset()
            rows = self._collect_label_rows(labels)
        if not rows:
            return _EMPTY_LABELS.copy()
        data = np.empty((len(rows), _LABEL_INSTANCE_FLOATS), dtype=np.float32)
        for index, (origin_x, origin_y, width, height, u0, v0, u1, v1, rgba) in enumerate(rows):
            data[index, 0] = origin_x
            data[index, 1] = origin_y
            data[index, 2] = width
            data[index, 3] = height
            data[index, 4] = u0
            data[index, 5] = v0
            data[index, 6] = u1
            data[index, 7] = v1
            data[index, 8] = rgba[0]
            data[index, 9] = rgba[1]
            data[index, 10] = rgba[2]
            data[index, 11] = rgba[3]
        return data

    def pack_marker_instances(self, markers: Sequence[GpuMarkerDraw]) -> np.ndarray:
        if not markers:
            return _EMPTY_MARKERS.copy()
        data = np.empty((len(markers), _MARKER_INSTANCE_FLOATS), dtype=np.float32)
        for index, marker in enumerate(markers):
            fill = _color_rgba(marker.fill)
            edge = _color_rgba(marker.edge)
            data[index, 0] = float(marker.x)
            data[index, 1] = float(marker.y)
            data[index, 2] = max(0.5, float(marker.radius))
            data[index, 3] = max(0.75, float(marker.stroke))
            data[index, 4] = fill[0]
            data[index, 5] = fill[1]
            data[index, 6] = fill[2]
            data[index, 7] = fill[3]
            data[index, 8] = edge[0]
            data[index, 9] = edge[1]
            data[index, 10] = edge[2]
            data[index, 11] = edge[3]
        return data

    def _collect_label_rows(
        self,
        labels: Sequence[GpuLabelDraw],
    ) -> list[tuple[float, float, float, float, float, float, float, float, tuple[float, float, float, float]]]:
        shadows: list[tuple[float, float, float, float, float, float, float, float, tuple[float, float, float, float]]] = []
        glyphs: list[tuple[float, float, float, float, float, float, float, float, tuple[float, float, float, float]]] = []
        atlas_w = float(self.atlas.width)
        atlas_h = float(self.atlas.height)
        for label in labels:
            text = str(label.text or "")
            if not text:
                continue
            sprite = self.atlas.sprite(label.font, text)
            if sprite is None:
                continue
            if label.baseline:
                origin_x = float(label.origin_x) - sprite.pad
                origin_y = float(label.origin_y) - sprite.ascent - sprite.pad
            else:
                origin_x = float(label.origin_x) - sprite.pad
                origin_y = float(label.origin_y) - sprite.pad
            # Upload flips the CPU atlas vertically, so CPU y=0 (top) is GL v=1.
            u0 = sprite.x / atlas_w
            u1 = (sprite.x + sprite.width) / atlas_w
            v0 = 1.0 - (sprite.y / atlas_h)
            v1 = 1.0 - ((sprite.y + sprite.height) / atlas_h)
            color = _color_rgba(label.color)
            quad = (
                origin_x,
                origin_y,
                float(sprite.width),
                float(sprite.height),
                u0,
                v0,
                u1,
                v1,
                color,
            )
            glyphs.append(quad)
            if label.shadow_color is not None:
                shadows.append(
                    (
                        origin_x + _SHADOW_OFFSET_PX,
                        origin_y + _SHADOW_OFFSET_PX,
                        float(sprite.width),
                        float(sprite.height),
                        u0,
                        v0,
                        u1,
                        v1,
                        _color_rgba(label.shadow_color),
                    )
                )
        return [*shadows, *glyphs]

    def draw(
        self,
        labels: Sequence[GpuLabelDraw],
        viewport_rect: QRectF,
        markers: Sequence[GpuMarkerDraw] = (),
    ) -> bool:
        metrics = LabelRendererMetrics(available=self._available, backend="instanced" if self._available else "cpu")
        metrics.label_count = len(labels)
        metrics.marker_count = len(markers)
        self.metrics = metrics
        if not labels and not markers:
            return True
        if not self._available or self._functions is None or self._label_program is None or self._marker_program is None:
            metrics.backend = "cpu"
            metrics.fallback_reason = self._disabled_reason or "renderer unavailable"
            self.metrics = metrics
            return False
        label_instances = self.pack_instances(labels)
        marker_instances = self.pack_marker_instances(markers)
        metrics.instance_count = int(label_instances.shape[0] + marker_instances.shape[0])
        metrics.glyph_count = int(label_instances.shape[0] // 2) if any(label.shadow_color is not None for label in labels) else int(label_instances.shape[0])
        metrics.atlas_glyph_count = self.atlas.glyph_count
        if label_instances.shape[0] <= 0 and marker_instances.shape[0] <= 0:
            self.metrics = metrics
            return True
        functions = self._functions
        gl = self._gl
        if gl is None:
            try:
                self._gl = self._import_opengl()
                gl = self._gl
            except Exception as exc:
                metrics.backend = "cpu"
                metrics.fallback_reason = f"PyOpenGL unavailable for draw: {exc}"
                self.metrics = metrics
                return False
        assert gl is not None
        previous_program = self._get_integerv(gl, _GL_CURRENT_PROGRAM, 1)[0]
        previous_active_texture = self._get_integerv(gl, _GL_ACTIVE_TEXTURE, 1)[0]
        previous_texture_2d = self._get_integerv(gl, _GL_TEXTURE_BINDING_2D, 1)[0]
        previous_array_buffer = self._get_integerv(gl, _GL_ARRAY_BUFFER_BINDING, 1)[0]
        blend_enabled = bool(gl.glIsEnabled(_GL_BLEND))
        depth_enabled = bool(gl.glIsEnabled(_GL_DEPTH_TEST))
        cull_enabled = bool(gl.glIsEnabled(_GL_CULL_FACE))
        scissor_enabled = bool(gl.glIsEnabled(_GL_SCISSOR_TEST))
        srgb_enabled = bool(gl.glIsEnabled(_GL_FRAMEBUFFER_SRGB))
        depth_mask = self._get_boolean(gl, _GL_DEPTH_WRITEMASK)
        blend_src = self._get_integerv(gl, _GL_BLEND_SRC, 1)[0]
        blend_dst = self._get_integerv(gl, _GL_BLEND_DST, 1)[0]
        blend_equation = self._get_integerv(gl, _GL_BLEND_EQUATION, 1)[0]
        try:
            if srgb_enabled:
                gl.glDisable(_GL_FRAMEBUFFER_SRGB)
            functions.glDisable(_GL_DEPTH_TEST)
            functions.glDepthMask(_GL_FALSE)
            functions.glDisable(_GL_CULL_FACE)
            functions.glDisable(_GL_SCISSOR_TEST)
            functions.glEnable(_GL_BLEND)
            functions.glBlendFunc(_GL_SRC_ALPHA, _GL_ONE_MINUS_SRC_ALPHA)
            functions.glBlendEquation(_GL_FUNC_ADD)
            upload_start = perf_counter()
            if label_instances.shape[0] > 0:
                self._upload_atlas(gl)
                self._upload_instances(self._label_instance_buffer, label_instances, "_label_instance_capacity", _LABEL_INSTANCE_STRIDE)
            if marker_instances.shape[0] > 0:
                self._upload_instances(self._marker_instance_buffer, marker_instances, "_marker_instance_capacity", _MARKER_INSTANCE_STRIDE)
            metrics.upload_seconds = perf_counter() - upload_start
            draw_start = perf_counter()
            if marker_instances.shape[0] > 0:
                self._draw_marker_instances(int(marker_instances.shape[0]), viewport_rect)
            if label_instances.shape[0] > 0:
                self._draw_label_instances(int(label_instances.shape[0]), viewport_rect)
            metrics.draw_seconds = perf_counter() - draw_start
            self.metrics = metrics
            return True
        except Exception as exc:
            metrics.backend = "cpu"
            metrics.fallback_reason = f"draw failed: {exc}"
            self.metrics = metrics
            _LOGGER.warning("SkyViewLabelRenderer draw failed: %s", exc)
            return False
        finally:
            try:
                if previous_array_buffer:
                    gl.glBindBuffer(_GL_ARRAY_BUFFER, int(previous_array_buffer))
                else:
                    gl.glBindBuffer(_GL_ARRAY_BUFFER, 0)
                functions.glActiveTexture(int(previous_active_texture) if previous_active_texture else _GL_TEXTURE0)
                functions.glBindTexture(_GL_TEXTURE_2D, int(previous_texture_2d))
                if previous_program:
                    gl.glUseProgram(int(previous_program))
                else:
                    gl.glUseProgram(0)
                if blend_enabled:
                    functions.glEnable(_GL_BLEND)
                else:
                    functions.glDisable(_GL_BLEND)
                functions.glBlendFunc(int(blend_src) if blend_src else _GL_SRC_ALPHA, int(blend_dst) if blend_dst else _GL_ONE_MINUS_SRC_ALPHA)
                if blend_equation:
                    functions.glBlendEquation(int(blend_equation))
                if depth_enabled:
                    functions.glEnable(_GL_DEPTH_TEST)
                else:
                    functions.glDisable(_GL_DEPTH_TEST)
                functions.glDepthMask(bool(depth_mask))
                if cull_enabled:
                    functions.glEnable(_GL_CULL_FACE)
                else:
                    functions.glDisable(_GL_CULL_FACE)
                if scissor_enabled:
                    functions.glEnable(_GL_SCISSOR_TEST)
                else:
                    functions.glDisable(_GL_SCISSOR_TEST)
                if srgb_enabled:
                    gl.glEnable(_GL_FRAMEBUFFER_SRGB)
                # Atlas uploads use ALIGNMENT=1. Qt's later QPainter footer/cardinal
                # glyphs need the default unpack layout or the text comes out scrambled.
                self._restore_qt_unpack(gl)
            except Exception:
                pass

    @staticmethod
    def _import_opengl():
        from OpenGL import GL

        return GL

    def _compile_program(self, vertex_source: str, fragment_source: str, name: str) -> QOpenGLShaderProgram:
        program = QOpenGLShaderProgram()
        if not program.addShaderFromSourceCode(QOpenGLShader.ShaderTypeBit.Vertex, vertex_source):
            raise RuntimeError(f"{name} vertex shader failed: {program.log().strip()}")
        if not program.addShaderFromSourceCode(QOpenGLShader.ShaderTypeBit.Fragment, fragment_source):
            raise RuntimeError(f"{name} fragment shader failed: {program.log().strip()}")
        if not program.link():
            raise RuntimeError(f"{name} program link failed: {program.log().strip()}")
        return program

    def _resolve_label_locations(self, program: QOpenGLShaderProgram) -> dict[str, int]:
        names = {
            "a_corner": program.attributeLocation("a_corner"),
            "a_origin": program.attributeLocation("a_origin"),
            "a_size": program.attributeLocation("a_size"),
            "a_uv0": program.attributeLocation("a_uv0"),
            "a_uv1": program.attributeLocation("a_uv1"),
            "a_color": program.attributeLocation("a_color"),
            "u_viewport_rect": program.uniformLocation("u_viewport_rect"),
            "u_atlas": program.uniformLocation("u_atlas"),
        }
        missing = [name for name, location in names.items() if location < 0]
        if missing:
            raise RuntimeError("Label shader missing locations: " + ", ".join(missing))
        return {name: int(location) for name, location in names.items()}

    def _resolve_marker_locations(self, program: QOpenGLShaderProgram) -> dict[str, int]:
        names = {
            "a_corner": program.attributeLocation("a_corner"),
            "a_center": program.attributeLocation("a_center"),
            "a_radius": program.attributeLocation("a_radius"),
            "a_stroke": program.attributeLocation("a_stroke"),
            "a_fill": program.attributeLocation("a_fill"),
            "a_edge": program.attributeLocation("a_edge"),
            "u_viewport_rect": program.uniformLocation("u_viewport_rect"),
        }
        missing = [name for name, location in names.items() if location < 0]
        if missing:
            raise RuntimeError("Marker shader missing locations: " + ", ".join(missing))
        return {name: int(location) for name, location in names.items()}

    def _create_quad_buffer(self, corners: tuple[tuple[float, float], ...]) -> QOpenGLBuffer:
        data = np.array(corners, dtype=np.float32)
        buffer = QOpenGLBuffer(QOpenGLBuffer.Type.VertexBuffer)
        if not buffer.create():
            raise RuntimeError("Failed to create overlay unit quad buffer")
        buffer.setUsagePattern(QOpenGLBuffer.UsagePattern.StaticDraw)
        buffer.bind()
        payload = bytes(data)
        buffer.allocate(len(payload))
        buffer.write(0, payload, len(payload))
        buffer.release()
        return buffer

    def _create_dynamic_buffer(self, name: str) -> QOpenGLBuffer:
        buffer = QOpenGLBuffer(QOpenGLBuffer.Type.VertexBuffer)
        if not buffer.create():
            raise RuntimeError(f"Failed to create {name} instance buffer")
        buffer.setUsagePattern(QOpenGLBuffer.UsagePattern.StreamDraw)
        return buffer

    def _create_atlas_texture(self) -> None:
        gl = self._gl
        if gl is None:
            raise RuntimeError("OpenGL bindings unavailable while creating label atlas")
        generated = gl.glGenTextures(1)
        texture_id = int(generated[0] if isinstance(generated, (list, tuple)) else generated)
        if texture_id <= 0:
            raise RuntimeError(f"glGenTextures returned invalid id {texture_id}")
        gl.glBindTexture(_GL_TEXTURE_2D, texture_id)
        gl.glTexParameteri(_GL_TEXTURE_2D, _GL_TEXTURE_MIN_FILTER, _GL_LINEAR)
        gl.glTexParameteri(_GL_TEXTURE_2D, _GL_TEXTURE_MAG_FILTER, _GL_LINEAR)
        gl.glTexParameteri(_GL_TEXTURE_2D, _GL_TEXTURE_WRAP_S, _GL_CLAMP_TO_EDGE)
        gl.glTexParameteri(_GL_TEXTURE_2D, _GL_TEXTURE_WRAP_T, _GL_CLAMP_TO_EDGE)
        self._reset_unpack(gl)
        zeros = np.zeros((self.atlas.height, self.atlas.width, 4), dtype=np.uint8)
        gl.glTexImage2D(
            _GL_TEXTURE_2D,
            0,
            _GL_RGBA8,
            self.atlas.width,
            self.atlas.height,
            0,
            _GL_RGBA,
            _GL_UNSIGNED_BYTE,
            zeros,
        )
        gl.glBindTexture(_GL_TEXTURE_2D, 0)
        self._texture_id = texture_id
        self.atlas._dirty = True

    def _probe_instancing(self) -> bool:
        try:
            gl = self._gl
            if gl is None:
                self._gl = self._import_opengl()
                gl = self._gl
            return all(
                callable(getattr(gl, name, None))
                for name in (
                    "glDrawArraysInstanced",
                    "glVertexAttribDivisor",
                    "glGenVertexArrays",
                    "glBindVertexArray",
                    "glDeleteVertexArrays",
                    "glTexImage2D",
                )
            )
        except Exception as exc:
            _LOGGER.warning("SkyViewLabelRenderer instancing probe failed: %s", exc)
            return False

    def _upload_instances(
        self,
        buffer: QOpenGLBuffer | None,
        instances: np.ndarray,
        capacity_field: str,
        stride: int,
    ) -> None:
        assert buffer is not None
        count = int(instances.shape[0])
        current = int(getattr(self, capacity_field))
        if count > current:
            current = max(256, int(math.ceil(count * 1.5)))
            setattr(self, capacity_field, current)
        buffer.bind()
        buffer.allocate(int(current * stride))
        if count > 0:
            payload = bytes(instances[:count])
            buffer.write(0, payload, len(payload))
        buffer.release()

    def _upload_atlas(self, gl) -> None:
        if self._texture_id <= 0:
            raise RuntimeError("label atlas texture is unavailable")
        if not self.atlas.take_dirty():
            return
        functions = self._functions
        assert functions is not None
        flipped = self.atlas.image.mirrored(False, True)
        pixels = _qimage_rgba(flipped)
        functions.glActiveTexture(_GL_TEXTURE0)
        functions.glBindTexture(_GL_TEXTURE_2D, int(self._texture_id))
        self._reset_unpack(gl)
        gl.glTexSubImage2D(
            _GL_TEXTURE_2D,
            0,
            0,
            0,
            self.atlas.width,
            self.atlas.height,
            _GL_RGBA,
            _GL_UNSIGNED_BYTE,
            pixels,
        )

    @staticmethod
    def _reset_unpack(gl) -> None:
        gl.glPixelStorei(_GL_UNPACK_ALIGNMENT, 1)
        gl.glPixelStorei(_GL_UNPACK_ROW_LENGTH, 0)
        gl.glPixelStorei(_GL_UNPACK_SKIP_ROWS, 0)
        gl.glPixelStorei(_GL_UNPACK_SKIP_PIXELS, 0)

    @staticmethod
    def _restore_qt_unpack(gl) -> None:
        gl.glPixelStorei(_GL_UNPACK_ALIGNMENT, 4)
        gl.glPixelStorei(_GL_UNPACK_ROW_LENGTH, 0)
        gl.glPixelStorei(_GL_UNPACK_SKIP_ROWS, 0)
        gl.glPixelStorei(_GL_UNPACK_SKIP_PIXELS, 0)

    def _draw_label_instances(self, count: int, viewport_rect: QRectF) -> None:
        if self._label_program is None or self._label_quad_buffer is None or self._label_instance_buffer is None:
            raise RuntimeError("label renderer is not initialized")
        functions = self._functions
        gl = self._gl
        assert functions is not None and gl is not None
        locations = self._label_locations
        self._ensure_vao_bound()
        try:
            self._label_program.bind()
            functions.glUniform4f(
                locations["u_viewport_rect"],
                float(viewport_rect.x()),
                float(viewport_rect.y()),
                float(viewport_rect.width()),
                float(viewport_rect.height()),
            )
            functions.glActiveTexture(_GL_TEXTURE0)
            functions.glBindTexture(_GL_TEXTURE_2D, int(self._texture_id))
            functions.glUniform1i(locations["u_atlas"], 0)
            corner_loc = locations["a_corner"]
            self._label_quad_buffer.bind()
            self._label_program.enableAttributeArray(corner_loc)
            self._label_program.setAttributeBuffer(corner_loc, _GL_FLOAT, 0, 2, 8)
            gl.glVertexAttribDivisor(int(corner_loc), 0)
            self._label_instance_buffer.bind()
            specs = (
                ("a_origin", 2, 0),
                ("a_size", 2, 8),
                ("a_uv0", 2, 16),
                ("a_uv1", 2, 24),
                ("a_color", 4, 32),
            )
            self._bind_attributes(self._label_program, locations, gl, specs, _LABEL_INSTANCE_STRIDE)
            gl.glDrawArraysInstanced(_GL_TRIANGLES, 0, 6, int(count))
            self._disable_attributes(self._label_program, locations, gl, ("a_origin", "a_size", "a_uv0", "a_uv1", "a_color"))
            self._label_program.disableAttributeArray(corner_loc)
            gl.glVertexAttribDivisor(int(corner_loc), 0)
            self._label_instance_buffer.release()
            self._label_quad_buffer.release()
            self._label_program.release()
        finally:
            self._release_vao_binding()

    def _draw_marker_instances(self, count: int, viewport_rect: QRectF) -> None:
        if self._marker_program is None or self._marker_quad_buffer is None or self._marker_instance_buffer is None:
            raise RuntimeError("marker renderer is not initialized")
        functions = self._functions
        gl = self._gl
        assert functions is not None and gl is not None
        locations = self._marker_locations
        self._ensure_vao_bound()
        try:
            self._marker_program.bind()
            functions.glUniform4f(
                locations["u_viewport_rect"],
                float(viewport_rect.x()),
                float(viewport_rect.y()),
                float(viewport_rect.width()),
                float(viewport_rect.height()),
            )
            corner_loc = locations["a_corner"]
            self._marker_quad_buffer.bind()
            self._marker_program.enableAttributeArray(corner_loc)
            self._marker_program.setAttributeBuffer(corner_loc, _GL_FLOAT, 0, 2, 8)
            gl.glVertexAttribDivisor(int(corner_loc), 0)
            self._marker_instance_buffer.bind()
            specs = (
                ("a_center", 2, 0),
                ("a_radius", 1, 8),
                ("a_stroke", 1, 12),
                ("a_fill", 4, 16),
                ("a_edge", 4, 32),
            )
            self._bind_attributes(self._marker_program, locations, gl, specs, _MARKER_INSTANCE_STRIDE)
            gl.glDrawArraysInstanced(_GL_TRIANGLES, 0, 6, int(count))
            self._disable_attributes(self._marker_program, locations, gl, ("a_center", "a_radius", "a_stroke", "a_fill", "a_edge"))
            self._marker_program.disableAttributeArray(corner_loc)
            gl.glVertexAttribDivisor(int(corner_loc), 0)
            self._marker_instance_buffer.release()
            self._marker_quad_buffer.release()
            self._marker_program.release()
        finally:
            self._release_vao_binding()

    def _bind_attributes(self, program, locations, gl, specs, stride: int) -> None:
        for name, size, offset in specs:
            loc = locations[name]
            program.enableAttributeArray(loc)
            program.setAttributeBuffer(loc, _GL_FLOAT, offset, size, stride)
            gl.glVertexAttribDivisor(loc, 1)

    def _disable_attributes(self, program, locations, gl, names: tuple[str, ...]) -> None:
        for name in names:
            loc = locations[name]
            program.disableAttributeArray(loc)
            try:
                gl.glVertexAttribDivisor(int(loc), 0)
            except Exception:
                pass

    def _ensure_vao_bound(self) -> None:
        gl = self._gl
        if gl is None:
            raise RuntimeError("OpenGL bindings unavailable while creating label VAO")
        stale_errors = self._consume_gl_errors()
        if stale_errors:
            _LOGGER.debug(
                "SkyViewLabelRenderer cleared pre-existing OpenGL error(s) before VAO bind: %s",
                ", ".join(f"0x{error:04x}" for error in stale_errors),
            )
        if self._gl_vao_id:
            gl.glBindVertexArray(int(self._gl_vao_id))
            bind_errors = self._consume_gl_errors()
            if bind_errors:
                raise RuntimeError(
                    "OpenGL error(s) binding label VAO: "
                    + ", ".join(f"0x{error:04x}" for error in bind_errors)
                )
            return
        generated = gl.glGenVertexArrays(1)
        vao_id = int(generated[0] if isinstance(generated, (list, tuple)) else generated)
        if vao_id <= 0:
            raise RuntimeError(f"glGenVertexArrays returned invalid id {vao_id}")
        gl.glBindVertexArray(vao_id)
        creation_errors = self._consume_gl_errors()
        if creation_errors:
            try:
                gl.glDeleteVertexArrays(1, [vao_id])
            except Exception:
                pass
            raise RuntimeError(
                "OpenGL error(s) creating label VAO: "
                + ", ".join(f"0x{error:04x}" for error in creation_errors)
            )
        self._gl_vao_id = vao_id

    def _release_vao_binding(self) -> None:
        if self._gl_vao_id and self._gl is not None:
            self._gl.glBindVertexArray(0)

    def _consume_gl_errors(self, *, limit: int = 16) -> tuple[int, ...]:
        functions = self._functions
        if functions is None:
            return ()
        errors: list[int] = []
        for _ in range(limit):
            try:
                error = int(functions.glGetError())
            except Exception:
                break
            if error == 0:
                break
            errors.append(error)
        return tuple(errors)

    @staticmethod
    def _get_integerv(gl, enum_value, count: int) -> list[int]:
        try:
            result = gl.glGetIntegerv(enum_value)
            if isinstance(result, (list, tuple)):
                return [int(value) for value in result[:count]]
            return [int(result)]
        except Exception:
            import ctypes

            array = (ctypes.c_int * count)()
            gl.glGetIntegerv(enum_value, array)
            return [int(array[index]) for index in range(count)]

    @staticmethod
    def _get_boolean(gl, enum_value) -> bool:
        try:
            value = gl.glGetBooleanv(enum_value)
            if isinstance(value, (list, tuple)):
                return bool(value[0])
            return bool(value)
        except Exception:
            return True
