"""Unified GPU star renderer for Sky Atlas (Stellarium-inspired PSF).

Formulas (defaults)
-------------------
Source luminosity proxy (Stellarium-like, then calibrated):
    lnL = -0.921034 * (magnitude + 12.12331) + ln(zoom_exposure)
    zoom_exposure = (60 / max(FOV_deg, 0.7)) ** 0.8
    calibration = 5.0 / exp(-0.921034 * 12.12331)
    luminosity = exp(lnL) * calibration
    compact_intensity = luminosity * (0.7 + 0.3 * contrast)

    At FOV 60 / contrast 1, magnitude 0 → intensity ≈ 5.

Compact radius uses a separate flux curve:
    size_flux = 10 ** (-0.4 * magnitude * (0.55 + 0.45 * contrast))
    raw_radius = (0.75 + 3.1 * size_flux**0.32) * star_size * dpr

Faint-star stability (physical framebuffer pixels; uploaded as logical = physical/dpr):
    logical_base = (0.75 + 3.1 * size_flux**0.32) * star_size
    raw_physical = logical_base * dpr
    if raw_physical < 0.3*dpr: omit (unless within limiting magnitude pad)
    elif raw_physical < 1.2*dpr: radius_physical = 1.2*dpr; intensity *= (raw/min)**3
    else: radius_physical = raw_physical
    if radius_physical > 8*dpr: compress extra growth with sqrt
    compact_radius_px (attribute) = radius_physical / dpr

Visibility fade multiplies intensity only (not compact footprint) near the limit.

Compact analytic PSF (fragment):
    core   = exp(-18 * r2)
    middle = 0.22 * exp(-5 * r2)
    wing   = 0.025 * exp(-1.3 * r2)
    mask   = smoothstep(1, 0.72, r2)
    psf    = (core + middle*mid_weight + wing*wing_weight) * mask

Blending: additive linear light (GL_ONE, GL_ONE). Stars accumulate into an
RGBA16F offscreen buffer, then a tone-map pass composites:
    mapped = 1 - exp(-hdr * exposure)
onto the QOpenGLWidget framebuffer.

Twinkle is a shader uniform time/seed modulation; the instance buffer is not
rebuilt for twinkle alone. Twinkle amplitude is zeroed when interaction is
not ``stable``.

Streaming strategy
------------------
Instance data is packed into a reused NumPy float32 array and uploaded with
buffer orphaning: ``allocate(capacity)`` then ``write(0, bytes)`` each frame
(equivalent to ``glBufferData`` NULL + ``glBufferSubData``). Capacity grows
with 1.5x hysteresis and never shrinks mid-session unless ``release()``.

OpenGL note
-----------
The rest of Sky View uses ``QOpenGLFunctions_2_0``. This renderer prefers true
instancing via PyOpenGL (``glVertexAttribDivisor`` + ``glDrawArraysInstanced``)
on compatibility contexts. If that fails, it falls back to one expanded-quad
draw call (still a single ``glDrawArrays``, no per-star Python GL calls).
"""

from __future__ import annotations

import logging
import math
from dataclasses import dataclass, replace
from time import perf_counter
from typing import Sequence

import numpy as np
from PySide6.QtCore import QRectF
from PySide6.QtGui import QColor
from PySide6.QtOpenGL import (
    QOpenGLBuffer,
    QOpenGLFramebufferObject,
    QOpenGLFramebufferObjectFormat,
    QOpenGLFunctions_2_0,
    QOpenGLShader,
    QOpenGLShaderProgram,
    QOpenGLTexture,
    QOpenGLVertexArrayObject,
)

_LOGGER = logging.getLogger(__name__)

# Instance layout uploaded to the GPU (float32, tightly packed):
# x_logical, y_logical, radius_logical_px, intensity, r, g, b, altitude_deg, seed, flags
# radius_logical_px = physical_radius_px / devicePixelRatio so projection stays in Qt logical space.
_INSTANCE_FLOATS = 10
_INSTANCE_STRIDE = _INSTANCE_FLOATS * 4
_INSTANCE_BYTES = _INSTANCE_STRIDE

# OpenGL enums (avoid relying on QOpenGLFunctions_* attribute exposure).
_GL_BLEND = 0x0BE2
_GL_BLEND_SRC = 0x0BE1
_GL_BLEND_DST = 0x0BE0
_GL_BLEND_EQUATION = 0x8009
_GL_FUNC_ADD = 0x8006
_GL_ONE = 0x0001
_GL_ZERO = 0x0000
_GL_DEPTH_TEST = 0x0B71
_GL_DEPTH_WRITEMASK = 0x0B72
_GL_CULL_FACE = 0x0B44
_GL_SCISSOR_TEST = 0x0C11
_GL_FRAMEBUFFER_SRGB = 0x8DB9
_GL_FRAMEBUFFER = 0x8D40
_GL_FRAMEBUFFER_BINDING = 0x8CA6
_GL_COLOR_BUFFER_BIT = 0x00004000
_GL_VIEWPORT = 0x0BA2
_GL_CURRENT_PROGRAM = 0x8B8D
_GL_ACTIVE_TEXTURE = 0x84E0
_GL_TEXTURE0 = 0x84C0
_GL_TEXTURE_2D = 0x0DE1
_GL_TEXTURE_BINDING_2D = 0x8069
_GL_ARRAY_BUFFER = 0x8892
_GL_ARRAY_BUFFER_BINDING = 0x8894
_GL_FLOAT = 0x1406
_GL_TRIANGLES = 0x0004
_GL_TRUE = 1
_GL_FALSE = 0

_FLAG_HALO = 1
_FLAG_SELECTED = 2

_BV_TABLE_SIZE = 128
_MAG_LUT_STEP = 0.05
_MAG_LUT_MIN = -2.0
_MAG_LUT_MAX = 16.0

BACKEND_INSTANCED = "instanced"
BACKEND_EXPANDED = "expanded"
BACKEND_LEGACY = "legacy"


@dataclass(slots=True)
class StarRendererSettings:
    field_width_deg: float = 95.0
    star_brightness: float = 1.0
    star_size: float = 1.0
    magnitude_size_contrast: float = 1.0
    limiting_magnitude: float = 8.7
    twinkle_amount: float = 0.5
    device_pixel_ratio: float = 1.0
    halo_threshold_magnitude: float = 1.8
    halo_intensity: float = 0.35
    halo_radius_scale: float = 4.5
    color_saturation: float = 0.55
    hdr_exposure: float = 1.15
    faint_min_radius_px: float = 1.2
    faint_omit_radius_px: float = 0.3
    bright_compress_radius_px: float = 8.0
    interaction_stable: bool = True
    time_seconds: float = 0.0
    moon_focus_dim: float = 1.0


@dataclass(slots=True)
class StarAppearanceSample:
    """Appearance sample.

    Radii:
    - raw_radius_physical_px: pre-footprint curve in framebuffer pixels
    - compact_radius_physical_px: after min-footprint / bright compression (framebuffer px)
    - compact_radius_px: logical pixels uploaded for projection (physical / dpr)
    - halo_* mirror the same conventions
    """

    compact_radius_px: float
    compact_intensity: float
    halo_radius_px: float
    halo_intensity: float
    visible: bool
    raw_radius_physical_px: float = 0.0
    compact_radius_physical_px: float = 0.0
    halo_radius_physical_px: float = 0.0


@dataclass(slots=True)
class StarRendererMetrics:
    candidate_count: int = 0
    visible_star_count: int = 0
    halo_star_count: int = 0
    prepare_seconds: float = 0.0
    upload_seconds: float = 0.0
    compact_draw_seconds: float = 0.0
    halo_draw_seconds: float = 0.0
    tonemap_seconds: float = 0.0
    draw_call_count: int = 0
    uploaded_bytes: int = 0
    used_instancing: bool = False
    backend: str = BACKEND_LEGACY
    cache_hit: bool = False
    upload_skipped: bool = False
    hdr_active: bool = False
    hdr_status: str = ""
    direct_additive_fallback: bool = False
    fallback_reason: str = ""
    gl_vendor: str = ""
    gl_renderer: str = ""
    gl_version: str = ""
    glsl_version: str = ""
    instancing_available: bool = False


@dataclass(slots=True)
class StarRendererDiagnostics:
    backend: str
    gl_vendor: str
    gl_renderer: str
    gl_version: str
    glsl_version: str
    instancing_available: bool
    hdr_fbo_complete: bool
    hdr_status: str
    direct_additive_fallback: bool
    disabled_reason: str = ""


@dataclass(frozen=True, slots=True)
class StarInstanceInput:
    x_px: float
    y_px: float
    magnitude: float
    color: str
    altitude_deg: float
    visibility: float
    star_id: int
    extinction: float = 1.0
    selected: bool = False
    bv_index: float | None = None


class StarRenderer:
    """Owns star shaders, buffers, magnitude/B−V LUTs, and HDR tonemap."""

    _COMPACT_VERTEX_SHADER = """
#version 120
attribute vec2 a_corner;
attribute vec2 a_center;
attribute float a_radius;
attribute float a_intensity;
attribute vec3 a_color;
attribute float a_altitude;
attribute float a_seed;

uniform vec4 u_viewport_rect;
uniform float u_dpr;
uniform float u_time;
uniform float u_twinkle_amount;
uniform float u_twinkle_enabled;

varying vec2 v_local;
varying vec3 v_rgb;
varying float v_intensity;
varying float v_brightness_weight;

float hash11(float n) {
    return fract(sin(n) * 43758.5453123);
}

void main() {
    float radius = max(a_radius, 0.35);
    vec2 logical_pos = a_center + (a_corner * radius);
    float safe_w = max(u_viewport_rect.z, 1.0e-6);
    float safe_h = max(u_viewport_rect.w, 1.0e-6);
    float clip_x = (((logical_pos.x - u_viewport_rect.x) / safe_w) * 2.0) - 1.0;
    float clip_y = 1.0 - (((logical_pos.y - u_viewport_rect.y) / safe_h) * 2.0);
    gl_Position = vec4(clip_x, clip_y, 0.0, 1.0);

    v_local = a_corner;
    float alt_sin = sin(clamp(a_altitude, -5.0, 90.0) * 0.01745329252);
    float horizon_weight = clamp(1.0 - 0.9 * max(alt_sin, 0.0), 0.1, 1.0);
    float twinkle = 1.0;
    if (u_twinkle_enabled > 0.5 && u_twinkle_amount > 1.0e-6 && a_intensity > 0.08) {
        float phase = a_seed * 6.2831853 + u_time * (1.7 + fract(a_seed * 13.0) * 1.3);
        float noise = 0.5 + 0.5 * sin(phase) * (0.65 + 0.35 * hash11(a_seed * 47.0 + floor(u_time * 8.0)));
        float amp = (0.06 + 0.04 * clamp(a_intensity * 2.0, 0.0, 1.0)) * u_twinkle_amount * 1.6 * horizon_weight;
        twinkle = clamp(1.0 - amp * (1.0 - noise), 0.88, 1.0);
    }
    v_intensity = a_intensity * twinkle;
    v_rgb = a_color;
    v_brightness_weight = clamp(a_intensity * 1.8, 0.0, 1.0);
}
"""

    _COMPACT_FRAGMENT_SHADER = """
#version 120
varying vec2 v_local;
varying vec3 v_rgb;
varying float v_intensity;
varying float v_brightness_weight;

void main() {
    float r2 = dot(v_local, v_local);
    if (r2 > 1.0) {
        discard;
    }
    float core = exp(-18.0 * r2);
    float middle = 0.22 * exp(-5.0 * r2);
    float wing = 0.025 * exp(-1.3 * r2);
    float mid_weight = mix(0.15, 1.0, v_brightness_weight);
    float wing_weight = mix(0.05, 0.85, v_brightness_weight * v_brightness_weight);
    float mask = smoothstep(1.0, 0.72, r2);
    float psf = (core + middle * mid_weight + wing * wing_weight) * mask;
    if (psf < 0.002) {
        discard;
    }
    vec3 rgb = v_rgb * (v_intensity * psf);
    gl_FragColor = vec4(rgb, 1.0);
}
"""

    _HALO_FRAGMENT_SHADER = """
#version 120
varying vec2 v_local;
varying vec3 v_rgb;
varying float v_intensity;
varying float v_brightness_weight;

void main() {
    float r2 = dot(v_local, v_local);
    if (r2 > 1.0) {
        discard;
    }
    float falloff = exp(-2.4 * r2) * (1.0 - smoothstep(0.55, 1.0, r2));
    if (falloff < 0.004) {
        discard;
    }
    vec3 rgb = v_rgb * (v_intensity * falloff * 0.55);
    gl_FragColor = vec4(rgb, 1.0);
}
"""

    _TONEMAP_VERTEX_SHADER = """
#version 120
attribute vec2 a_pos;
attribute vec2 a_uv;
varying vec2 v_uv;
void main() {
    v_uv = a_uv;
    gl_Position = vec4(a_pos, 0.0, 1.0);
}
"""

    _TONEMAP_FRAGMENT_SHADER = """
#version 120
uniform sampler2D u_hdr;
uniform float u_exposure;
varying vec2 v_uv;
void main() {
    vec3 hdr = texture2D(u_hdr, v_uv).rgb;
    vec3 mapped = 1.0 - exp(-hdr * u_exposure);
    // Output additive contribution in approximately display-referred space.
    gl_FragColor = vec4(mapped, 1.0);
}
"""

    def __init__(self) -> None:
        self._available = False
        self._disabled_reason = ""
        self._functions: QOpenGLFunctions_2_0 | None = None
        self._compact_program: QOpenGLShaderProgram | None = None
        self._halo_program: QOpenGLShaderProgram | None = None
        self._tonemap_program: QOpenGLShaderProgram | None = None
        self._quad_buffer: QOpenGLBuffer | None = None
        self._instance_buffer: QOpenGLBuffer | None = None
        self._halo_instance_buffer: QOpenGLBuffer | None = None
        self._tonemap_quad_buffer: QOpenGLBuffer | None = None
        self._hdr_fbo: QOpenGLFramebufferObject | None = None
        self._bv_texture: QOpenGLTexture | None = None
        self._instance_capacity = 0
        self._halo_capacity = 0
        self._cpu_instances = np.zeros((0, _INSTANCE_FLOATS), dtype=np.float32)
        self._cpu_halo_instances = np.zeros((0, _INSTANCE_FLOATS), dtype=np.float32)
        self._settings = StarRendererSettings()
        self._mag_lut: tuple[StarAppearanceSample, ...] = ()
        self._mag_lut_key: tuple[object, ...] | None = None
        self._bv_table = self._build_bv_color_table()
        self._use_instancing = False
        self._vao: QOpenGLVertexArrayObject | None = None
        self._gl_vao_id: int = 0
        self._require_vao = True
        self._instancing_disabled_permanently = False
        self._gl = None
        self.metrics = StarRendererMetrics()
        self._locations: dict[str, dict[str, int]] = {}
        self._default_fbo_handle = 0
        self._gl_vendor = ""
        self._gl_renderer = ""
        self._gl_version = ""
        self._glsl_version = ""
        self._hdr_status = "uninitialized"
        self._last_uploaded_compact_key: tuple[object, ...] | None = None
        self._last_uploaded_halo_key: tuple[object, ...] | None = None
        self._gpu_has_compact = False
        self._gpu_has_halo = False
        self._linear_rgb_cache: dict[tuple[str, float | None, float], tuple[float, float, float]] = {}

    @property
    def available(self) -> bool:
        return self._available

    @property
    def disabled_reason(self) -> str:
        return self._disabled_reason

    @property
    def backend_name(self) -> str:
        if not self._available:
            return BACKEND_LEGACY
        return BACKEND_INSTANCED if self._use_instancing else BACKEND_EXPANDED

    def diagnostics(self) -> StarRendererDiagnostics:
        hdr_complete = bool(self._hdr_fbo is not None and self._hdr_fbo.isValid() and self._hdr_status.startswith("complete"))
        return StarRendererDiagnostics(
            backend=self.backend_name,
            gl_vendor=self._gl_vendor,
            gl_renderer=self._gl_renderer,
            gl_version=self._gl_version,
            glsl_version=self._glsl_version,
            instancing_available=self._use_instancing,
            hdr_fbo_complete=hdr_complete,
            hdr_status=self._hdr_status,
            direct_additive_fallback=not hdr_complete and self._available,
            disabled_reason=self._disabled_reason,
        )

    def initialize(self, functions: QOpenGLFunctions_2_0 | None, *, default_framebuffer: int = 0) -> bool:
        if functions is None:
            self._available = False
            self._disabled_reason = "OpenGL functions unavailable"
            _LOGGER.warning("StarRenderer initialize failed: %s", self._disabled_reason)
            return False
        if self._available and self._compact_program is not None:
            return True
        self._functions = functions
        self._default_fbo_handle = int(default_framebuffer)
        try:
            self._gl = None
            self._capture_gl_info()
            self._compact_program = self._compile_program(self._COMPACT_VERTEX_SHADER, self._COMPACT_FRAGMENT_SHADER)
            self._halo_program = self._compile_program(self._COMPACT_VERTEX_SHADER, self._HALO_FRAGMENT_SHADER)
            self._tonemap_program = self._compile_program(self._TONEMAP_VERTEX_SHADER, self._TONEMAP_FRAGMENT_SHADER)
            self._locations = {
                "compact": self._resolve_program_locations(self._compact_program, instance=True),
                "halo": self._resolve_program_locations(self._halo_program, instance=True),
                "tonemap": self._resolve_tonemap_locations(self._tonemap_program),
            }
            self._quad_buffer = self._create_unit_quad_buffer()
            self._tonemap_quad_buffer = self._create_fullscreen_quad_buffer()
            self._instance_buffer = self._create_dynamic_buffer()
            self._halo_instance_buffer = self._create_dynamic_buffer()
            self._bv_texture = self._create_bv_texture()
            try:
                self._gl = self._import_opengl()
            except Exception as exc:
                _LOGGER.warning("StarRenderer PyOpenGL import failed; expanded backend only: %s", exc)
                self._gl = None
            self._use_instancing = (
                False if self._instancing_disabled_permanently else self._probe_instancing()
            )
            # Defer VAO creation until the first draw() under beginNativePainting
            # so the object is created with the paint drawable's context current.
            self._vao = None
            if not self._use_instancing:
                _LOGGER.warning(
                    "StarRenderer fallback backend=expanded "
                    "(vendor=%s renderer=%s version=%s)",
                    self._gl_vendor,
                    self._gl_renderer,
                    self._gl_version,
                )
            else:
                _LOGGER.info(
                    "StarRenderer backend=instanced vendor=%s renderer=%s version=%s glsl=%s",
                    self._gl_vendor,
                    self._gl_renderer,
                    self._gl_version,
                    self._glsl_version,
                )
            self._rebuild_magnitude_lut(self._settings)
            self._available = True
            self._disabled_reason = ""
            return True
        except Exception as exc:
            reason = f"shader/resource init failed: {exc}"
            self.release()
            self._available = False
            self._disabled_reason = reason
            _LOGGER.warning("StarRenderer initialize failed: %s", reason)
            return False

    def resize(self, width_px: int, height_px: int, device_pixel_ratio: float) -> None:
        self._settings.device_pixel_ratio = max(1.0e-6, float(device_pixel_ratio))
        width = max(1, int(width_px))
        height = max(1, int(height_px))
        if self._hdr_fbo is not None and self._hdr_fbo.width() == width and self._hdr_fbo.height() == height:
            return
        self._destroy_hdr_fbo()
        if not self._available:
            self._hdr_status = "renderer unavailable"
            return
        fmt = QOpenGLFramebufferObjectFormat()
        fmt.setAttachment(QOpenGLFramebufferObject.Attachment.NoAttachment)
        fmt.setInternalTextureFormat(0x881A)  # GL_RGBA16F
        fmt.setSamples(0)
        try:
            self._hdr_fbo = QOpenGLFramebufferObject(width, height, fmt)
        except Exception as exc:
            self._hdr_fbo = None
            self._hdr_status = f"create_exception:{exc}"
            _LOGGER.warning("StarRenderer HDR FBO create failed; using direct-additive fallback: %s", exc)
            return
        if self._hdr_fbo is None or not self._hdr_fbo.isValid():
            self._hdr_fbo = None
            self._hdr_status = "invalid"
            _LOGGER.warning("StarRenderer HDR FBO invalid; using direct-additive fallback")
            return
        status = self._query_framebuffer_status()
        self._hdr_status = status
        if not status.startswith("complete"):
            _LOGGER.warning(
                "StarRenderer HDR FBO incomplete (%s); destroying and using direct-additive fallback",
                status,
            )
            self._destroy_hdr_fbo()
            self._hdr_status = f"incomplete:{status}"
        else:
            _LOGGER.info("StarRenderer HDR FBO complete (%sx%s status=%s)", width, height, status)

    def release(self) -> None:
        self._available = False
        self._use_instancing = False
        self._last_uploaded_compact_key = None
        self._last_uploaded_halo_key = None
        self._gpu_has_compact = False
        self._gpu_has_halo = False
        if self._vao is not None:
            try:
                self._vao.destroy()
            except Exception:
                pass
            self._vao = None
        if self._gl_vao_id and self._gl is not None:
            try:
                self._gl.glDeleteVertexArrays(1, [int(self._gl_vao_id)])
            except Exception:
                pass
            self._gl_vao_id = 0
        for program in (self._compact_program, self._halo_program, self._tonemap_program):
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
        self._compact_program = None
        self._halo_program = None
        self._tonemap_program = None
        for buffer in (self._quad_buffer, self._instance_buffer, self._halo_instance_buffer, self._tonemap_quad_buffer):
            if buffer is None:
                continue
            try:
                buffer.destroy()
            except Exception:
                pass
        self._quad_buffer = None
        self._instance_buffer = None
        self._halo_instance_buffer = None
        self._tonemap_quad_buffer = None
        if self._bv_texture is not None:
            try:
                self._bv_texture.destroy()
            except Exception:
                pass
            self._bv_texture = None
        self._destroy_hdr_fbo()
        self._hdr_status = "released"
        self._instance_capacity = 0
        self._halo_capacity = 0
        self._cpu_instances = np.zeros((0, _INSTANCE_FLOATS), dtype=np.float32)
        self._cpu_halo_instances = np.zeros((0, _INSTANCE_FLOATS), dtype=np.float32)
        self._locations = {}
        self._functions = None
        self._gl = None

    def apply_settings(self, **kwargs: object) -> None:
        geometry_keys = {
            "field_width_deg",
            "star_brightness",
            "star_size",
            "magnitude_size_contrast",
            "limiting_magnitude",
            "device_pixel_ratio",
            "halo_threshold_magnitude",
            "halo_intensity",
            "halo_radius_scale",
            "color_saturation",
            "hdr_exposure",
            "faint_min_radius_px",
            "faint_omit_radius_px",
            "bright_compress_radius_px",
            "moon_focus_dim",
        }
        touched_geometry = False
        for key, value in kwargs.items():
            if hasattr(self._settings, key):
                current = getattr(self._settings, key)
                if current != value:
                    setattr(self._settings, key, value)  # type: ignore[misc]
                    if key in geometry_keys:
                        touched_geometry = True
        if touched_geometry:
            self._rebuild_magnitude_lut(self._settings)
            self._last_uploaded_compact_key = None
            self._last_uploaded_halo_key = None
            if "color_saturation" in kwargs:
                self._linear_rgb_cache.clear()

    def appearance_for_magnitude(self, magnitude: float, *, visibility: float = 1.0) -> StarAppearanceSample:
        sample = self._sample_magnitude_lut(float(magnitude))
        if not sample.visible:
            return sample
        visibility_clamped = max(0.0, min(1.0, float(visibility)))
        intensity = sample.compact_intensity * visibility_clamped
        return replace(
            sample,
            compact_intensity=intensity,
            halo_intensity=sample.halo_intensity * visibility_clamped,
            # Footprint stays fixed under visibility fade.
            visible=intensity > 1.0e-5,
        )

    def pack_instances(
        self,
        stars: Sequence[StarInstanceInput],
        settings: StarRendererSettings | None = None,
    ) -> tuple[np.ndarray, np.ndarray]:
        """Pack visible stars into reused CPU instance arrays (no per-star Python GL)."""
        prepare_start = perf_counter()
        if settings is not None:
            self.apply_settings(**{field: getattr(settings, field) for field in settings.__dataclass_fields__})
        count = len(stars)
        if self._cpu_instances.shape[0] < count:
            capacity = max(256, int(math.ceil(count * 1.5)))
            self._cpu_instances = np.zeros((capacity, _INSTANCE_FLOATS), dtype=np.float32)
        compact = self._cpu_instances
        halo_rows: list[np.ndarray] = []
        written = 0
        saturation = max(0.0, min(1.5, float(self._settings.color_saturation)))
        moon_dim = max(0.05, min(1.0, float(self._settings.moon_focus_dim)))
        for star in stars:
            appearance = self.appearance_for_magnitude(star.magnitude, visibility=star.visibility)
            if not appearance.visible and not star.selected:
                continue
            intensity = appearance.compact_intensity * max(0.0, float(star.extinction)) * moon_dim
            intensity *= max(0.35, min(2.0, float(self._settings.star_brightness)))
            # Upload logical radii for projection against logical viewport_rect.
            radius = appearance.compact_radius_px
            if star.selected and not appearance.visible:
                intensity = max(intensity, 0.12)
                radius = max(radius, self._settings.faint_min_radius_px)
            if intensity <= 1.0e-6:
                continue
            rgb = self._resolve_linear_rgb(star.color, star.bv_index, saturation)
            seed = float((int(star.star_id) * 2654435761) & 0xFFFFFFFF) / 4294967295.0
            flags = float(_FLAG_SELECTED if star.selected else 0)
            compact[written, 0] = float(star.x_px)
            compact[written, 1] = float(star.y_px)
            compact[written, 2] = float(radius)
            compact[written, 3] = float(intensity)
            compact[written, 4] = rgb[0]
            compact[written, 5] = rgb[1]
            compact[written, 6] = rgb[2]
            compact[written, 7] = float(star.altitude_deg)
            compact[written, 8] = seed
            compact[written, 9] = flags
            if appearance.halo_intensity > 1.0e-5 and star.magnitude <= self._settings.halo_threshold_magnitude:
                halo_row = np.array(
                    (
                        star.x_px,
                        star.y_px,
                        appearance.halo_radius_px,
                        appearance.halo_intensity * intensity / max(appearance.compact_intensity, 1.0e-6),
                        rgb[0],
                        rgb[1],
                        rgb[2],
                        star.altitude_deg,
                        seed,
                        float(_FLAG_HALO | (int(flags))),
                    ),
                    dtype=np.float32,
                )
                halo_rows.append(halo_row)
            written += 1
        if self._cpu_halo_instances.shape[0] < len(halo_rows):
            self._cpu_halo_instances = np.zeros((max(64, int(math.ceil(len(halo_rows) * 1.5))), _INSTANCE_FLOATS), dtype=np.float32)
        if halo_rows:
            halo_array = np.stack(halo_rows, axis=0)
            self._cpu_halo_instances[: len(halo_rows)] = halo_array
        compact = self._cpu_instances[:written]
        halo = self._cpu_halo_instances[: len(halo_rows)]
        self.metrics.prepare_seconds = perf_counter() - prepare_start
        self.metrics.visible_star_count = written
        self.metrics.halo_star_count = len(halo_rows)
        self.metrics.candidate_count = count
        return compact, halo

    def draw(
        self,
        *,
        viewport_rect: QRectF,
        device_pixel_ratio: float,
        compact_instances: np.ndarray,
        halo_instances: np.ndarray,
        settings: StarRendererSettings | None = None,
        default_framebuffer: int | None = None,
        geometry_content_key: tuple[object, ...] | None = None,
    ) -> StarRendererMetrics:
        metrics = StarRendererMetrics()
        metrics.gl_vendor = self._gl_vendor
        metrics.gl_renderer = self._gl_renderer
        metrics.gl_version = self._gl_version
        metrics.glsl_version = self._glsl_version
        metrics.instancing_available = self._use_instancing
        metrics.backend = self.backend_name
        self.metrics = metrics
        if not self._available or self._functions is None:
            metrics.backend = BACKEND_LEGACY
            metrics.fallback_reason = self._disabled_reason or "renderer unavailable"
            return metrics
        if settings is not None:
            self.apply_settings(**{field: getattr(settings, field) for field in settings.__dataclass_fields__})
        self._settings.device_pixel_ratio = max(1.0e-6, float(device_pixel_ratio))
        dpr = self._settings.device_pixel_ratio
        physical_w = max(1, int(math.ceil(viewport_rect.width() * dpr)))
        physical_h = max(1, int(math.ceil(viewport_rect.height() * dpr)))
        try:
            if self._hdr_fbo is None or self._hdr_fbo.width() != physical_w or self._hdr_fbo.height() != physical_h:
                self.resize(physical_w, physical_h, dpr)
        except Exception as exc:
            self._destroy_hdr_fbo()
            self._hdr_status = f"resize_exception:{exc}"
            _LOGGER.warning("StarRenderer HDR resize failed; direct-additive fallback: %s", exc)
        functions = self._functions
        gl = self._gl
        if gl is None:
            try:
                self._gl = self._import_opengl()
                gl = self._gl
            except Exception as exc:
                metrics.backend = BACKEND_LEGACY
                metrics.fallback_reason = f"PyOpenGL unavailable for draw: {exc}"
                self.metrics = metrics
                return metrics
        target_fbo = self._default_fbo_handle if default_framebuffer is None else int(default_framebuffer)
        previous_viewport = self._get_integerv(gl, _GL_VIEWPORT, 4)
        previous_program = self._get_integerv(gl, _GL_CURRENT_PROGRAM, 1)[0]
        previous_active_texture = self._get_integerv(gl, _GL_ACTIVE_TEXTURE, 1)[0]
        previous_texture_2d = self._get_integerv(gl, _GL_TEXTURE_BINDING_2D, 1)[0]
        previous_array_buffer = self._get_integerv(gl, _GL_ARRAY_BUFFER_BINDING, 1)[0]
        previous_framebuffer = self._get_integerv(gl, _GL_FRAMEBUFFER_BINDING, 1)[0]
        blend_enabled = bool(gl.glIsEnabled(_GL_BLEND))
        depth_enabled = bool(gl.glIsEnabled(_GL_DEPTH_TEST))
        cull_enabled = bool(gl.glIsEnabled(_GL_CULL_FACE))
        scissor_enabled = bool(gl.glIsEnabled(_GL_SCISSOR_TEST))
        srgb_enabled = bool(gl.glIsEnabled(_GL_FRAMEBUFFER_SRGB))
        depth_mask = self._get_boolean(gl, _GL_DEPTH_WRITEMASK)
        blend_src = self._get_integerv(gl, _GL_BLEND_SRC, 1)[0]
        blend_dst = self._get_integerv(gl, _GL_BLEND_DST, 1)[0]
        blend_equation = self._get_integerv(gl, _GL_BLEND_EQUATION, 1)[0]
        use_hdr = self._hdr_fbo is not None and self._hdr_fbo.isValid() and self._hdr_status.startswith("complete")
        metrics.hdr_active = use_hdr
        metrics.hdr_status = self._hdr_status
        metrics.direct_additive_fallback = not use_hdr
        content_key = geometry_content_key
        if content_key is None:
            content_key = (
                int(compact_instances.shape[0]),
                int(halo_instances.shape[0]),
                int(compact_instances.nbytes),
                int(halo_instances.nbytes),
                float(np.sum(compact_instances)) if compact_instances.size else 0.0,
                float(np.sum(halo_instances)) if halo_instances.size else 0.0,
            )
        skip_compact_upload = (
            self._gpu_has_compact
            and content_key == self._last_uploaded_compact_key
            and int(compact_instances.shape[0]) > 0
        )
        skip_halo_upload = (
            self._gpu_has_halo
            and content_key == self._last_uploaded_halo_key
            and int(halo_instances.shape[0]) >= 0
        )
        try:
            if srgb_enabled:
                gl.glDisable(_GL_FRAMEBUFFER_SRGB)
            if use_hdr:
                self._hdr_fbo.bind()
                functions.glViewport(0, 0, physical_w, physical_h)
                functions.glClearColor(0.0, 0.0, 0.0, 0.0)
                functions.glClear(_GL_COLOR_BUFFER_BIT)
            else:
                functions.glViewport(0, 0, physical_w, physical_h)
            functions.glDisable(_GL_DEPTH_TEST)
            functions.glDepthMask(_GL_FALSE)
            functions.glDisable(_GL_CULL_FACE)
            functions.glDisable(_GL_SCISSOR_TEST)
            functions.glEnable(_GL_BLEND)
            functions.glBlendFunc(_GL_ONE, _GL_ONE)
            functions.glBlendEquation(_GL_FUNC_ADD)

            metrics.visible_star_count = int(compact_instances.shape[0])
            metrics.halo_star_count = int(halo_instances.shape[0])
            metrics.used_instancing = self._use_instancing
            metrics.backend = self.backend_name

            if compact_instances.shape[0] > 0:
                if skip_compact_upload:
                    metrics.upload_skipped = True
                    metrics.cache_hit = True
                else:
                    upload_start = perf_counter()
                    self._upload_instances(self._instance_buffer, compact_instances, is_halo=False)
                    metrics.upload_seconds += perf_counter() - upload_start
                    metrics.uploaded_bytes += int(compact_instances.nbytes)
                    self._last_uploaded_compact_key = content_key
                    self._gpu_has_compact = True
                # Keep CPU cache aligned with the arrays used for this draw.
                if compact_instances is not self._cpu_instances[: compact_instances.shape[0]]:
                    need = int(compact_instances.shape[0])
                    if self._cpu_instances.shape[0] < need:
                        self._cpu_instances = np.zeros((max(256, need * 2), _INSTANCE_FLOATS), dtype=np.float32)
                    self._cpu_instances[:need] = compact_instances
                draw_start = perf_counter()
                self._draw_instance_pass(
                    program_key="compact",
                    program=self._compact_program,
                    instance_buffer=self._instance_buffer,
                    cpu_instances=self._cpu_instances[: int(compact_instances.shape[0])],
                    count=int(compact_instances.shape[0]),
                    viewport_rect=viewport_rect,
                )
                metrics.compact_draw_seconds = perf_counter() - draw_start
                metrics.draw_call_count += 1

            if halo_instances.shape[0] > 0:
                if skip_halo_upload and skip_compact_upload:
                    metrics.upload_skipped = True
                    metrics.cache_hit = True
                else:
                    upload_start = perf_counter()
                    self._upload_instances(self._halo_instance_buffer, halo_instances, is_halo=True)
                    metrics.upload_seconds += perf_counter() - upload_start
                    metrics.uploaded_bytes += int(halo_instances.nbytes)
                    self._last_uploaded_halo_key = content_key
                    self._gpu_has_halo = True
                if halo_instances is not self._cpu_halo_instances[: halo_instances.shape[0]]:
                    need = int(halo_instances.shape[0])
                    if self._cpu_halo_instances.shape[0] < need:
                        self._cpu_halo_instances = np.zeros(
                            (max(64, need * 2), _INSTANCE_FLOATS), dtype=np.float32
                        )
                    self._cpu_halo_instances[:need] = halo_instances
                draw_start = perf_counter()
                self._draw_instance_pass(
                    program_key="halo",
                    program=self._halo_program,
                    instance_buffer=self._halo_instance_buffer,
                    cpu_instances=self._cpu_halo_instances[: int(halo_instances.shape[0])],
                    count=int(halo_instances.shape[0]),
                    viewport_rect=viewport_rect,
                )
                metrics.halo_draw_seconds = perf_counter() - draw_start
                metrics.draw_call_count += 1
            else:
                self._gpu_has_halo = False
                self._last_uploaded_halo_key = content_key

            if use_hdr:
                QOpenGLFramebufferObject.bindDefault()
                if target_fbo:
                    # QOpenGLFunctions_2_0 has no glBindFramebuffer — use PyOpenGL.
                    gl.glBindFramebuffer(_GL_FRAMEBUFFER, int(target_fbo))
                functions.glViewport(0, 0, physical_w, physical_h)
                tonemap_start = perf_counter()
                self._draw_tonemap_pass()
                metrics.tonemap_seconds = perf_counter() - tonemap_start
                metrics.draw_call_count += 1
        finally:
            try:
                gl.glBindFramebuffer(_GL_FRAMEBUFFER, int(previous_framebuffer))
            except Exception:
                try:
                    QOpenGLFramebufferObject.bindDefault()
                except Exception:
                    pass
            try:
                if previous_program:
                    gl.glUseProgram(int(previous_program))
                else:
                    gl.glUseProgram(0)
            except Exception:
                try:
                    if previous_program:
                        functions.glUseProgram(int(previous_program))
                    else:
                        functions.glUseProgram(0)
                except Exception:
                    pass
            try:
                gl.glActiveTexture(int(previous_active_texture) if previous_active_texture else _GL_TEXTURE0)
                gl.glBindTexture(_GL_TEXTURE_2D, int(previous_texture_2d))
            except Exception:
                try:
                    functions.glActiveTexture(
                        int(previous_active_texture) if previous_active_texture else _GL_TEXTURE0
                    )
                    functions.glBindTexture(_GL_TEXTURE_2D, int(previous_texture_2d))
                except Exception:
                    pass
            try:
                gl.glBindBuffer(_GL_ARRAY_BUFFER, int(previous_array_buffer))
            except Exception:
                pass
            functions.glBlendFunc(int(blend_src) if blend_src else _GL_ONE, int(blend_dst) if blend_dst else _GL_ZERO)
            try:
                functions.glBlendEquation(int(blend_equation) if blend_equation else _GL_FUNC_ADD)
            except Exception:
                pass
            if blend_enabled:
                functions.glEnable(_GL_BLEND)
            else:
                functions.glDisable(_GL_BLEND)
            if depth_enabled:
                functions.glEnable(_GL_DEPTH_TEST)
            else:
                functions.glDisable(_GL_DEPTH_TEST)
            if cull_enabled:
                functions.glEnable(_GL_CULL_FACE)
            else:
                functions.glDisable(_GL_CULL_FACE)
            if scissor_enabled:
                functions.glEnable(_GL_SCISSOR_TEST)
            else:
                functions.glDisable(_GL_SCISSOR_TEST)
            if srgb_enabled:
                try:
                    gl.glEnable(_GL_FRAMEBUFFER_SRGB)
                except Exception:
                    pass
            else:
                try:
                    gl.glDisable(_GL_FRAMEBUFFER_SRGB)
                except Exception:
                    pass
            functions.glDepthMask(_GL_TRUE if depth_mask else _GL_FALSE)
            if previous_viewport is not None:
                functions.glViewport(
                    int(previous_viewport[0]),
                    int(previous_viewport[1]),
                    int(previous_viewport[2]),
                    int(previous_viewport[3]),
                )
            self._check_gl_error("star renderer draw")
        metrics.backend = self.backend_name if self._available else BACKEND_LEGACY
        metrics.used_instancing = self._use_instancing
        self.metrics = metrics
        return metrics

    def metrics_log_line(self) -> str:
        m = self.metrics
        return (
            f"backend={m.backend} stars={m.visible_star_count} halo={m.halo_star_count} "
            f"prep={m.prepare_seconds * 1000.0:.2f}ms "
            f"upload={m.upload_seconds * 1000.0:.2f}ms "
            f"upload_skipped={int(m.upload_skipped)} "
            f"gpu={(m.compact_draw_seconds + m.halo_draw_seconds + m.tonemap_seconds) * 1000.0:.2f}ms "
            f"draws={m.draw_call_count} bytes={m.uploaded_bytes} "
            f"hdr={int(m.hdr_active)} hdr_status={m.hdr_status or '-'} "
            f"direct_additive={int(m.direct_additive_fallback)}"
        )

    @staticmethod
    def uploaded_bytes_for_star_counts(visible_stars: int, halo_stars: int = 0) -> int:
        return max(0, int(visible_stars)) * _INSTANCE_BYTES + max(0, int(halo_stars)) * _INSTANCE_BYTES

    # --- magnitude / color model -------------------------------------------------

    def _rebuild_magnitude_lut(self, settings: StarRendererSettings) -> None:
        key = (
            round(settings.field_width_deg, 4),
            round(settings.star_brightness, 4),
            round(settings.star_size, 4),
            round(settings.magnitude_size_contrast, 4),
            round(settings.limiting_magnitude, 4),
            round(settings.device_pixel_ratio, 4),
            round(settings.halo_threshold_magnitude, 4),
            round(settings.halo_intensity, 4),
            round(settings.halo_radius_scale, 4),
            round(settings.faint_min_radius_px, 4),
            round(settings.faint_omit_radius_px, 4),
            round(settings.bright_compress_radius_px, 4),
        )
        if key == self._mag_lut_key and self._mag_lut:
            return
        samples: list[StarAppearanceSample] = []
        magnitude = _MAG_LUT_MIN
        while magnitude <= _MAG_LUT_MAX + 1.0e-9:
            samples.append(self._compute_appearance(magnitude, settings))
            magnitude += _MAG_LUT_STEP
        self._mag_lut = tuple(samples)
        self._mag_lut_key = key

    def _sample_magnitude_lut(self, magnitude: float) -> StarAppearanceSample:
        if not self._mag_lut:
            self._rebuild_magnitude_lut(self._settings)
        position = (float(magnitude) - _MAG_LUT_MIN) / _MAG_LUT_STEP
        index0 = int(math.floor(position))
        index0 = max(0, min(len(self._mag_lut) - 1, index0))
        index1 = max(0, min(len(self._mag_lut) - 1, index0 + 1))
        sample0 = self._mag_lut[index0]
        sample1 = self._mag_lut[index1]
        if index0 == index1:
            return sample0
        frac = max(0.0, min(1.0, position - index0))
        return StarAppearanceSample(
            compact_radius_px=sample0.compact_radius_px + (sample1.compact_radius_px - sample0.compact_radius_px) * frac,
            compact_intensity=sample0.compact_intensity + (sample1.compact_intensity - sample0.compact_intensity) * frac,
            halo_radius_px=sample0.halo_radius_px + (sample1.halo_radius_px - sample0.halo_radius_px) * frac,
            halo_intensity=sample0.halo_intensity + (sample1.halo_intensity - sample0.halo_intensity) * frac,
            visible=sample0.visible or sample1.visible,
            raw_radius_physical_px=sample0.raw_radius_physical_px
            + (sample1.raw_radius_physical_px - sample0.raw_radius_physical_px) * frac,
            compact_radius_physical_px=sample0.compact_radius_physical_px
            + (sample1.compact_radius_physical_px - sample0.compact_radius_physical_px) * frac,
            halo_radius_physical_px=sample0.halo_radius_physical_px
            + (sample1.halo_radius_physical_px - sample0.halo_radius_physical_px) * frac,
        )

    @staticmethod
    def _compute_appearance(magnitude: float, settings: StarRendererSettings) -> StarAppearanceSample:
        fov = max(0.7, float(settings.field_width_deg))
        zoom_exposure = math.pow(60.0 / fov, 0.8)
        contrast = max(0.4, min(2.0, float(settings.magnitude_size_contrast)))
        dpr = max(1.0e-6, float(settings.device_pixel_ratio))
        # Stellarium-inspired luminosity proxy, then calibrated so mag 0 at FOV 60
        # yields compact_intensity ≈ 5 under default contrast.
        ln_l = -0.921034 * (float(magnitude) + 12.12331) + math.log(max(1.0e-6, zoom_exposure))
        raw_l = math.exp(ln_l)
        calibration = 5.0 / max(1.0e-12, math.exp(-0.921034 * 12.12331))
        luminosity = raw_l * calibration
        intensity = luminosity * (0.7 + 0.3 * contrast)
        size_flux = math.pow(10.0, -0.4 * float(magnitude) * (0.55 + 0.45 * contrast))
        # Logical size curve, then convert to physical framebuffer pixels for stability rules.
        raw_logical = (0.75 + 3.1 * math.pow(max(size_flux, 1.0e-9), 0.32)) * max(
            0.4, min(2.5, float(settings.star_size))
        )
        raw_physical = raw_logical * dpr
        omit_physical = float(settings.faint_omit_radius_px) * dpr
        min_physical = float(settings.faint_min_radius_px) * dpr
        if raw_physical < omit_physical and magnitude > settings.limiting_magnitude + 0.5:
            return StarAppearanceSample(
                0.0,
                0.0,
                0.0,
                0.0,
                False,
                raw_radius_physical_px=raw_physical,
                compact_radius_physical_px=0.0,
                halo_radius_physical_px=0.0,
            )
        working_physical = raw_physical
        if working_physical < omit_physical:
            working_physical = omit_physical
        if working_physical < min_physical:
            intensity *= math.pow(working_physical / min_physical, 3.0)
            radius_physical = min_physical
        else:
            radius_physical = working_physical
        compress_at = float(settings.bright_compress_radius_px) * dpr
        if radius_physical > compress_at:
            radius_physical = compress_at + math.sqrt(radius_physical - compress_at)
        radius_logical = radius_physical / dpr
        if intensity < 1.0e-5:
            return StarAppearanceSample(
                radius_logical,
                0.0,
                0.0,
                0.0,
                False,
                raw_radius_physical_px=raw_physical,
                compact_radius_physical_px=radius_physical,
                halo_radius_physical_px=0.0,
            )
        halo_intensity = 0.0
        halo_radius_physical = 0.0
        halo_radius_logical = 0.0
        if magnitude <= settings.halo_threshold_magnitude:
            halo_intensity = float(settings.halo_intensity) * math.pow(max(luminosity / 5.0, 1.0e-9), 0.35)
            halo_radius_physical = radius_physical * float(settings.halo_radius_scale)
            halo_radius_logical = halo_radius_physical / dpr
        return StarAppearanceSample(
            radius_logical,
            intensity,
            halo_radius_logical,
            halo_intensity,
            True,
            raw_radius_physical_px=raw_physical,
            compact_radius_physical_px=radius_physical,
            halo_radius_physical_px=halo_radius_physical,
        )

    @staticmethod
    def _build_bv_color_table() -> np.ndarray:
        """128-entry desaturated B−V → linear RGB table."""
        table = np.zeros((_BV_TABLE_SIZE, 3), dtype=np.float32)
        for index in range(_BV_TABLE_SIZE):
            bv = -0.4 + (index / max(1, _BV_TABLE_SIZE - 1)) * 2.4
            hex_color = _bv_to_hex(bv)
            table[index] = _srgb_hex_to_linear(hex_color)
        return table

    def _resolve_linear_rgb(self, color: str, bv_index: float | None, saturation: float) -> tuple[float, float, float]:
        cache_key = (str(color), None if bv_index is None else round(float(bv_index), 3), round(float(saturation), 3))
        cached = self._linear_rgb_cache.get(cache_key)
        if cached is not None:
            return cached
        if bv_index is not None:
            normalized = (float(bv_index) + 0.4) / 2.4
            index = int(round(max(0.0, min(1.0, normalized)) * (_BV_TABLE_SIZE - 1)))
            rgb = self._bv_table[index]
        else:
            rgb = _srgb_hex_to_linear(color)
        white = np.array((0.96, 0.97, 1.0), dtype=np.float32)
        mixed = white * (1.0 - saturation) + rgb * saturation
        resolved = (float(mixed[0]), float(mixed[1]), float(mixed[2]))
        self._linear_rgb_cache[cache_key] = resolved
        return resolved

    # --- GL helpers --------------------------------------------------------------

    @staticmethod
    def _import_opengl():
        from OpenGL import GL

        return GL

    def _compile_program(self, vertex_source: str, fragment_source: str) -> QOpenGLShaderProgram:
        program = QOpenGLShaderProgram()
        if not program.addShaderFromSourceCode(QOpenGLShader.ShaderTypeBit.Vertex, vertex_source):
            raise RuntimeError(f"Star vertex shader failed: {program.log().strip()}")
        if not program.addShaderFromSourceCode(QOpenGLShader.ShaderTypeBit.Fragment, fragment_source):
            raise RuntimeError(f"Star fragment shader failed: {program.log().strip()}")
        if not program.link():
            raise RuntimeError(f"Star program link failed: {program.log().strip()}")
        return program

    def _resolve_program_locations(self, program: QOpenGLShaderProgram, *, instance: bool) -> dict[str, int]:
        names = {
            "a_corner": program.attributeLocation("a_corner"),
            "a_center": program.attributeLocation("a_center"),
            "a_radius": program.attributeLocation("a_radius"),
            "a_intensity": program.attributeLocation("a_intensity"),
            "a_color": program.attributeLocation("a_color"),
            "a_altitude": program.attributeLocation("a_altitude"),
            "a_seed": program.attributeLocation("a_seed"),
            "u_viewport_rect": program.uniformLocation("u_viewport_rect"),
            "u_dpr": program.uniformLocation("u_dpr"),
            "u_time": program.uniformLocation("u_time"),
            "u_twinkle_amount": program.uniformLocation("u_twinkle_amount"),
            "u_twinkle_enabled": program.uniformLocation("u_twinkle_enabled"),
        }
        missing = [name for name, location in names.items() if location < 0 and name.startswith("a_")]
        if missing:
            raise RuntimeError("Star shader missing attributes: " + ", ".join(missing))
        return {name: int(location) for name, location in names.items()}

    def _resolve_tonemap_locations(self, program: QOpenGLShaderProgram) -> dict[str, int]:
        names = {
            "a_pos": program.attributeLocation("a_pos"),
            "a_uv": program.attributeLocation("a_uv"),
            "u_hdr": program.uniformLocation("u_hdr"),
            "u_exposure": program.uniformLocation("u_exposure"),
        }
        missing = [name for name, location in names.items() if location < 0]
        if missing:
            raise RuntimeError("Tonemap shader missing: " + ", ".join(missing))
        return {name: int(location) for name, location in names.items()}

    def _create_unit_quad_buffer(self) -> QOpenGLBuffer:
        corners = np.array(
            (
                (-1.0, -1.0),
                (1.0, -1.0),
                (-1.0, 1.0),
                (-1.0, 1.0),
                (1.0, -1.0),
                (1.0, 1.0),
            ),
            dtype=np.float32,
        )
        buffer = QOpenGLBuffer(QOpenGLBuffer.Type.VertexBuffer)
        if not buffer.create():
            raise RuntimeError("Failed to create star unit quad buffer")
        buffer.setUsagePattern(QOpenGLBuffer.UsagePattern.StaticDraw)
        buffer.bind()
        payload = bytes(corners)
        buffer.allocate(len(payload))
        buffer.write(0, payload, len(payload))
        buffer.release()
        return buffer

    def _create_fullscreen_quad_buffer(self) -> QOpenGLBuffer:
        # pos.xy, uv.xy
        data = np.array(
            (
                (-1.0, -1.0, 0.0, 0.0),
                (1.0, -1.0, 1.0, 0.0),
                (-1.0, 1.0, 0.0, 1.0),
                (-1.0, 1.0, 0.0, 1.0),
                (1.0, -1.0, 1.0, 0.0),
                (1.0, 1.0, 1.0, 1.0),
            ),
            dtype=np.float32,
        )
        buffer = QOpenGLBuffer(QOpenGLBuffer.Type.VertexBuffer)
        if not buffer.create():
            raise RuntimeError("Failed to create tonemap quad buffer")
        buffer.setUsagePattern(QOpenGLBuffer.UsagePattern.StaticDraw)
        buffer.bind()
        payload = bytes(data)
        buffer.allocate(len(payload))
        buffer.write(0, payload, len(payload))
        buffer.release()
        return buffer

    def _create_dynamic_buffer(self) -> QOpenGLBuffer:
        buffer = QOpenGLBuffer(QOpenGLBuffer.Type.VertexBuffer)
        if not buffer.create():
            raise RuntimeError("Failed to create star instance buffer")
        buffer.setUsagePattern(QOpenGLBuffer.UsagePattern.StreamDraw)
        return buffer

    def _create_bv_texture(self) -> QOpenGLTexture:
        image = np.zeros((_BV_TABLE_SIZE, 1, 4), dtype=np.float32)
        image[:, 0, 0:3] = self._bv_table
        image[:, 0, 3] = 1.0
        texture = QOpenGLTexture(QOpenGLTexture.Target.Target2D)
        texture.create()
        texture.setSize(_BV_TABLE_SIZE, 1)
        texture.setFormat(QOpenGLTexture.TextureFormat.RGBA16F)
        texture.allocateStorage()
        texture.setData(
            0,
            QOpenGLTexture.PixelFormat.RGBA,
            QOpenGLTexture.PixelType.Float32,
            image.tobytes(),
        )
        texture.setMinificationFilter(QOpenGLTexture.Filter.Nearest)
        texture.setMagnificationFilter(QOpenGLTexture.Filter.Nearest)
        texture.setWrapMode(QOpenGLTexture.WrapMode.ClampToEdge)
        return texture

    def _capture_gl_info(self) -> None:
        functions = self._functions
        if functions is None:
            return

        def _string(enum_value: int) -> str:
            try:
                value = functions.glGetString(enum_value)
            except Exception:
                return ""
            if value is None:
                return ""
            if isinstance(value, bytes):
                return value.decode("utf-8", errors="replace")
            return str(value)

        # Prefer Qt OpenGL function tables only — PyOpenGL string queries can AV
        # on some offscreen / partial contexts.
        self._gl_vendor = _string(0x1F00)  # GL_VENDOR
        self._gl_renderer = _string(0x1F01)  # GL_RENDERER
        self._gl_version = _string(0x1F02)  # GL_VERSION
        self._glsl_version = _string(0x8B8C)  # GL_SHADING_LANGUAGE_VERSION

    def _probe_instancing(self) -> bool:
        try:
            gl = self._gl
            if gl is None:
                self._gl = self._import_opengl()
                gl = self._gl
            return callable(getattr(gl, "glDrawArraysInstanced", None)) and callable(
                getattr(gl, "glVertexAttribDivisor", None)
            )
        except Exception as exc:
            _LOGGER.warning("StarRenderer instancing probe failed: %s", exc)
            return False

    def _query_framebuffer_status(self) -> str:
        functions = self._functions
        gl = self._gl
        if functions is None:
            return "no_functions"
        try:
            status = int(functions.glCheckFramebufferStatus(0x8D40))  # GL_FRAMEBUFFER
        except Exception:
            if gl is None:
                return "check_failed"
            try:
                status = int(gl.glCheckFramebufferStatus(0x8D40))
            except Exception as exc:
                return f"check_exception:{exc}"
        names = {
            0x8CD5: "complete",
            0x8CD6: "incomplete_attachment",
            0x8CD7: "incomplete_missing_attachment",
            0x8CD9: "incomplete_dimensions",
            0x8CDD: "unsupported",
            0x8CDB: "incomplete_draw_buffer",
            0x8CDC: "incomplete_read_buffer",
            0x8219: "incomplete_multisample",
            0x8DA8: "incomplete_layer_targets",
        }
        return names.get(status, f"0x{status:04x}")

    def _ensure_capacity(self, buffer: QOpenGLBuffer | None, capacity_field: str, count: int) -> None:
        assert buffer is not None
        current = int(getattr(self, capacity_field))
        if count <= current:
            return
        new_capacity = max(256, int(math.ceil(count * 1.5)))
        buffer.bind()
        buffer.allocate(int(new_capacity * _INSTANCE_STRIDE))
        buffer.release()
        setattr(self, capacity_field, new_capacity)

    def _upload_instances(self, buffer: QOpenGLBuffer | None, instances: np.ndarray, *, is_halo: bool) -> None:
        assert buffer is not None
        count = int(instances.shape[0])
        self._ensure_capacity(buffer, "_halo_capacity" if is_halo else "_instance_capacity", count)
        # Buffer orphaning: re-allocate same capacity to avoid GPU stalls, then write.
        capacity = self._halo_capacity if is_halo else self._instance_capacity
        buffer.bind()
        buffer.allocate(int(capacity * _INSTANCE_STRIDE))
        if count > 0:
            payload = bytes(instances[:count])
            buffer.write(0, payload, len(payload))
        buffer.release()

    def _draw_instance_pass(
        self,
        *,
        program_key: str,
        program: QOpenGLShaderProgram | None,
        instance_buffer: QOpenGLBuffer | None,
        cpu_instances: np.ndarray,
        count: int,
        viewport_rect: QRectF,
    ) -> None:
        if program is None or instance_buffer is None or self._quad_buffer is None or count <= 0:
            return
        functions = self._functions
        gl = self._gl
        assert functions is not None and gl is not None
        locations = self._locations[program_key]
        program.bind()
        program.setUniformValue(
            locations["u_viewport_rect"],
            float(viewport_rect.x()),
            float(viewport_rect.y()),
            float(viewport_rect.width()),
            float(viewport_rect.height()),
        )
        program.setUniformValue(locations["u_dpr"], float(self._settings.device_pixel_ratio))
        program.setUniformValue(locations["u_time"], float(self._settings.time_seconds))
        program.setUniformValue(locations["u_twinkle_amount"], float(self._settings.twinkle_amount))
        program.setUniformValue(
            locations["u_twinkle_enabled"],
            1.0 if self._settings.interaction_stable else 0.0,
        )

        if self._use_instancing:
            try:
                self._draw_instanced(program, locations, instance_buffer, count, gl)
            except Exception as exc:
                _LOGGER.warning(
                    "StarRenderer instanced draw failed (%s); permanently falling back to expanded",
                    exc,
                )
                self._use_instancing = False
                self._instancing_disabled_permanently = True
                self._draw_expanded(program, locations, cpu_instances, count, gl)
        else:
            self._draw_expanded(program, locations, cpu_instances, count, gl)
        program.release()

    def _ensure_vao_bound(self) -> None:
        """Bind a VAO when required; no-op on compatibility profiles that allow non-VAO draws."""
        gl = self._gl
        if self._gl_vao_id and gl is not None:
            try:
                gl.glBindVertexArray(int(self._gl_vao_id))
                return
            except Exception:
                self._gl_vao_id = 0
        if self._vao is not None and self._vao.isCreated():
            if self._vao.bind():
                return
            try:
                self._vao.destroy()
            except Exception:
                pass
            self._vao = None
        # Prefer raw GL VAOs — QOpenGLVertexArrayObject.bind() is unreliable with
        # QPainter beginNativePainting on some NVIDIA/Qt6 setups.
        if gl is not None and callable(getattr(gl, "glGenVertexArrays", None)):
            try:
                generated = gl.glGenVertexArrays(1)
                vao_id = int(generated[0] if isinstance(generated, (list, tuple)) else generated)
                gl.glBindVertexArray(vao_id)
                self._gl_vao_id = vao_id
                return
            except Exception as exc:
                _LOGGER.warning("StarRenderer glGenVertexArrays failed: %s", exc)
        try:
            self._vao = QOpenGLVertexArrayObject()
            if self._vao.create() and self._vao.bind():
                return
        except Exception as exc:
            _LOGGER.warning("StarRenderer Qt VAO create/bind failed: %s", exc)
        self._vao = None
        self._gl_vao_id = 0
        # Compatibility-profile contexts can draw without a VAO.
        self._require_vao = False
        self._use_instancing = False
        self._instancing_disabled_permanently = True
        _LOGGER.warning("StarRenderer drawing without VAO (compat fallback; expanded backend)")

    def _release_vao_binding(self) -> None:
        if self._gl_vao_id and self._gl is not None:
            try:
                self._gl.glBindVertexArray(0)
            except Exception:
                pass
            return
        if self._vao is not None:
            try:
                self._vao.release()
            except Exception:
                pass

    def _draw_instanced(self, program, locations, instance_buffer, count, gl) -> None:
        assert self._quad_buffer is not None
        self._ensure_vao_bound()
        if not self._require_vao and not self._gl_vao_id and self._vao is None:
            raise RuntimeError("instanced draw requires a VAO")
        try:
            corner_loc = locations["a_corner"]
            self._quad_buffer.bind()
            program.enableAttributeArray(corner_loc)
            program.setAttributeBuffer(corner_loc, _GL_FLOAT, 0, 2, 8)
            gl.glVertexAttribDivisor(int(corner_loc), 0)

            instance_buffer.bind()
            self._bind_instance_attributes(program, locations, gl, divisor=1)
            gl.glDrawArraysInstanced(_GL_TRIANGLES, 0, 6, count)
            self._disable_instance_attributes(program, locations, gl)
            program.disableAttributeArray(corner_loc)
            gl.glVertexAttribDivisor(int(corner_loc), 0)
            instance_buffer.release()
            self._quad_buffer.release()
        finally:
            self._release_vao_binding()

    def _draw_expanded(self, program, locations, instances, count, gl) -> None:
        """Fallback: expand instances on CPU into one triangle list, one draw call."""
        if instances is None or int(getattr(instances, "shape", [0])[0]) < count:
            raise RuntimeError(
                f"expanded draw missing CPU instances (have={0 if instances is None else instances.shape[0]} need={count})"
            )
        corners = np.array(
            ((-1.0, -1.0), (1.0, -1.0), (-1.0, 1.0), (-1.0, 1.0), (1.0, -1.0), (1.0, 1.0)),
            dtype=np.float32,
        )
        expanded = np.empty((count * 6, _INSTANCE_FLOATS + 2), dtype=np.float32)
        packed = np.asarray(instances[:count], dtype=np.float32)
        for corner_index, corner in enumerate(corners):
            expanded[corner_index::6, 0:2] = corner
            expanded[corner_index::6, 2:] = packed
        scratch = QOpenGLBuffer(QOpenGLBuffer.Type.VertexBuffer)
        if not scratch.create():
            raise RuntimeError("Failed to create expanded star buffer")
        scratch.setUsagePattern(QOpenGLBuffer.UsagePattern.StreamDraw)
        self._ensure_vao_bound()
        try:
            scratch.bind()
            payload = bytes(expanded)
            scratch.allocate(len(payload))
            scratch.write(0, payload, len(payload))
            stride = (_INSTANCE_FLOATS + 2) * 4
            program.enableAttributeArray(locations["a_corner"])
            program.setAttributeBuffer(locations["a_corner"], _GL_FLOAT, 0, 2, stride)
            self._bind_expanded_attributes(program, locations, stride)
            gl.glDrawArrays(_GL_TRIANGLES, 0, count * 6)
            self._disable_instance_attributes(program, locations, gl)
            program.disableAttributeArray(locations["a_corner"])
            scratch.release()
        finally:
            try:
                scratch.destroy()
            except Exception:
                pass
            self._release_vao_binding()

    def _bind_instance_attributes(self, program, locations, gl, *, divisor: int) -> None:
        # Instance float layout (40 bytes). flags at offset 36 is CPU/metadata padding
        # and is intentionally not bound (unused by the compact PSF shader).
        specs = (
            ("a_center", 2, 0),
            ("a_radius", 1, 8),
            ("a_intensity", 1, 12),
            ("a_color", 3, 16),
            ("a_altitude", 1, 28),
            ("a_seed", 1, 32),
        )
        for name, size, offset in specs:
            loc = locations[name]
            program.enableAttributeArray(loc)
            program.setAttributeBuffer(loc, _GL_FLOAT, offset, size, _INSTANCE_STRIDE)
            gl.glVertexAttribDivisor(loc, divisor)

    def _bind_expanded_attributes(self, program, locations, stride: int) -> None:
        specs = (
            ("a_center", 2, 8),
            ("a_radius", 1, 16),
            ("a_intensity", 1, 20),
            ("a_color", 3, 24),
            ("a_altitude", 1, 36),
            ("a_seed", 1, 40),
        )
        for name, size, offset in specs:
            loc = locations[name]
            program.enableAttributeArray(loc)
            program.setAttributeBuffer(loc, _GL_FLOAT, offset, size, stride)

    def _disable_instance_attributes(self, program, locations, gl) -> None:
        for name in ("a_center", "a_radius", "a_intensity", "a_color", "a_altitude", "a_seed"):
            loc = locations[name]
            program.disableAttributeArray(loc)
            if self._use_instancing:
                try:
                    gl.glVertexAttribDivisor(int(loc), 0)
                except Exception:
                    pass

    def _draw_tonemap_pass(self) -> None:
        if self._tonemap_program is None or self._tonemap_quad_buffer is None or self._hdr_fbo is None:
            return
        functions = self._functions
        assert functions is not None
        locations = self._locations["tonemap"]
        self._tonemap_program.bind()
        functions.glActiveTexture(_GL_TEXTURE0)
        functions.glBindTexture(_GL_TEXTURE_2D, int(self._hdr_fbo.texture()))
        self._tonemap_program.setUniformValue(locations["u_hdr"], 0)
        self._tonemap_program.setUniformValue(locations["u_exposure"], float(self._settings.hdr_exposure))
        functions.glBlendFunc(_GL_ONE, _GL_ONE)
        self._tonemap_quad_buffer.bind()
        self._tonemap_program.enableAttributeArray(locations["a_pos"])
        self._tonemap_program.setAttributeBuffer(locations["a_pos"], _GL_FLOAT, 0, 2, 16)
        self._tonemap_program.enableAttributeArray(locations["a_uv"])
        self._tonemap_program.setAttributeBuffer(locations["a_uv"], _GL_FLOAT, 8, 2, 16)
        functions.glDrawArrays(_GL_TRIANGLES, 0, 6)
        self._tonemap_program.disableAttributeArray(locations["a_pos"])
        self._tonemap_program.disableAttributeArray(locations["a_uv"])
        self._tonemap_quad_buffer.release()
        functions.glBindTexture(_GL_TEXTURE_2D, 0)
        self._tonemap_program.release()

    def _destroy_hdr_fbo(self) -> None:
        if self._hdr_fbo is not None:
            try:
                self._hdr_fbo.release()
            except Exception:
                pass
            self._hdr_fbo = None

    def _check_gl_error(self, context: str) -> None:
        functions = self._functions
        if functions is None:
            return
        error = int(functions.glGetError())
        if error:
            raise RuntimeError(f"OpenGL error in {context}: 0x{error:04x}")

    @staticmethod
    def _get_integerv(gl, enum_value, count: int) -> list[int]:
        from OpenGL.raw.GL.VERSION.GL_1_1 import arrays as _arrays  # noqa: F401

        values = (gl.constants.GLint * count)() if hasattr(gl, "constants") else None
        try:
            result = gl.glGetIntegerv(enum_value)
            if isinstance(result, (list, tuple)):
                return [int(v) for v in result[:count]]
            return [int(result)]
        except Exception:
            import ctypes

            arr = (ctypes.c_int * count)()
            gl.glGetIntegerv(enum_value, arr)
            return [int(arr[i]) for i in range(count)]

    @staticmethod
    def _get_boolean(gl, enum_value) -> bool:
        try:
            value = gl.glGetBooleanv(enum_value)
            if isinstance(value, (list, tuple)):
                return bool(value[0])
            return bool(value)
        except Exception:
            return True


class StarHitGrid:
    """Compact screen-space uniform grid for star hit testing."""

    def __init__(self, cell_size_px: float = 20.0) -> None:
        self.cell_size_px = max(8.0, float(cell_size_px))
        self._cells: dict[tuple[int, int], list[tuple[float, float, float, object]]] = {}

    def clear(self) -> None:
        self._cells.clear()

    def insert(self, x: float, y: float, radius: float, payload: object) -> None:
        cell = self._cell_key(x, y)
        self._cells.setdefault(cell, []).append((float(x), float(y), float(radius), payload))

    def query(self, x: float, y: float) -> list[tuple[float, float, float, object]]:
        cx, cy = self._cell_key(x, y)
        hits: list[tuple[float, float, float, object]] = []
        for ox in (-1, 0, 1):
            for oy in (-1, 0, 1):
                hits.extend(self._cells.get((cx + ox, cy + oy), ()))
        return hits

    def _cell_key(self, x: float, y: float) -> tuple[int, int]:
        size = self.cell_size_px
        return int(math.floor(x / size)), int(math.floor(y / size))


def metadata_does_not_affect_psf(
    color: str,
    magnitude: float,
    *,
    searchable: bool,
    selectable: bool,
    label_visible: bool,
    settings: StarRendererSettings | None = None,
) -> StarAppearanceSample:
    """Public helper used by tests: metadata flags must not change compact PSF params."""
    renderer = StarRenderer()
    renderer._rebuild_magnitude_lut(settings or StarRendererSettings())
    base = renderer.appearance_for_magnitude(magnitude)
    # Color/search flags intentionally unused — same magnitude ⇒ same sample.
    _ = (color, searchable, selectable, label_visible)
    return base


def _srgb_channel_to_linear(channel: float) -> float:
    value = max(0.0, min(1.0, float(channel)))
    if value <= 0.04045:
        return value / 12.92
    return math.pow((value + 0.055) / 1.055, 2.4)


def _srgb_hex_to_linear(color: str) -> np.ndarray:
    qcolor = QColor(color)
    if not qcolor.isValid():
        qcolor = QColor("#f8fbff")
    return np.array(
        (
            _srgb_channel_to_linear(qcolor.redF()),
            _srgb_channel_to_linear(qcolor.greenF()),
            _srgb_channel_to_linear(qcolor.blueF()),
        ),
        dtype=np.float32,
    )


def _bv_to_hex(bv_index: float) -> str:
    anchors = (
        (-0.15, (176, 205, 255)),
        (0.0, (213, 231, 255)),
        (0.32, (247, 249, 255)),
        (0.62, (255, 236, 198)),
        (0.95, (255, 202, 141)),
        (1.45, (255, 169, 103)),
    )
    clamped = max(anchors[0][0], min(anchors[-1][0], float(bv_index)))
    for (lower_bv, lower_color), (upper_bv, upper_color) in zip(anchors, anchors[1:]):
        if clamped > upper_bv:
            continue
        t = 0.0 if abs(upper_bv - lower_bv) <= 1.0e-9 else (clamped - lower_bv) / (upper_bv - lower_bv)
        red = int(round(lower_color[0] + (upper_color[0] - lower_color[0]) * t))
        green = int(round(lower_color[1] + (upper_color[1] - lower_color[1]) * t))
        blue = int(round(lower_color[2] + (upper_color[2] - lower_color[2]) * t))
        return f"#{red:02x}{green:02x}{blue:02x}"
    red, green, blue = anchors[-1][1]
    return f"#{red:02x}{green:02x}{blue:02x}"
