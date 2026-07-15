from __future__ import annotations

from collections import OrderedDict
from concurrent.futures import Future, ThreadPoolExecutor
import math
from dataclasses import dataclass
from pathlib import Path
from time import perf_counter

import numpy as np

from PySide6.QtCore import QRectF
from PySide6.QtCore import QRect, QRectF
from PySide6.QtGui import QImage, QPainter
from PySide6.QtOpenGL import QOpenGLFunctions_2_0, QOpenGLShader, QOpenGLShaderProgram, QOpenGLTexture

from photometry_app.core.benchmarking import BENCHMARK_ENABLED, get_benchmark_recorder
from photometry_app.core.milky_way_mask import (
    DEFAULT_MILKY_WAY_MASK_PARAMETERS,
    apply_milky_way_alpha_mask,
)
from photometry_app.core.qt_image_formats import qt_image_decode_failure_reason


@dataclass(slots=True)
class SkyViewMilkyWayGlUniforms:

    viewport_rect: QRectF

    viewport_width_px: int

    viewport_height_px: int

    device_pixel_ratio: float

    equatorial_forward: tuple[float, float, float]

    equatorial_up: tuple[float, float, float]

    equatorial_right: tuple[float, float, float]

    half_horizontal_fov_rad: float

    half_vertical_fov_rad: float

    field_width_deg: float

    opacity: float

    brightness: float

    contrast: float

    saturation: float

    black_point: float

    gamma: float

    flip_horizontal: bool

    alpha_mode: str = "structure_preserving"

    alpha_mask_black: float = DEFAULT_MILKY_WAY_MASK_PARAMETERS.black_point

    alpha_mask_white: float = DEFAULT_MILKY_WAY_MASK_PARAMETERS.white_point


@dataclass(frozen=True, slots=True)
class SkyViewMilkyWayTileMeshVertex:

    clip_x: float

    clip_y: float

    texture_u: float

    texture_v: float


@dataclass(frozen=True, slots=True)
class SkyViewMilkyWayTileDrawRequest:

    texture_path: Path

    texture_cache_key: tuple[object, ...]

    level: int

    x_index: int

    y_index: int

    vertices: tuple[SkyViewMilkyWayTileMeshVertex, ...]

    is_missing: bool = False

    has_signal: bool | None = None

    alpha_coverage: float = 1.0

    mean_alpha: float = 1.0

    max_alpha: float = 1.0

    tile_renderer: str = "shader"

    uv_bounds: tuple[float, float, float, float] = (0.0, 1.0, 0.0, 1.0)

    ra_bounds_deg: tuple[float, float] = (0.0, 0.0)

    dec_bounds_deg: tuple[float, float] = (-90.0, 90.0)

    ra_wrap: bool = False

    include_max_v: bool = False

    tile_grid_shape: tuple[int, int] = (1, 1)

    clip_bounds: tuple[float, float, float, float] = (-1.0, 1.0, -1.0, 1.0)

    subdivision_columns: int = 0

    subdivision_rows: int = 0

    vertex_count: int = 0

    triangle_count: int = 0

    skipped_triangle_count: int = 0

    invalid_vertex_count: int = 0

    degenerate_triangle_count: int = 0

    discontinuity_triangle_count: int = 0

    large_area_triangle_count: int = 0

    padded_tile: bool = False

    gutter_pixels: int = 0

    content_region: tuple[int, int, int, int] = (0, 0, 0, 0)

    wrap_split_count: int = 0

    max_triangle_area: float = 0.0


@dataclass(slots=True)
class _SkyViewMilkyWayTextureCacheEntry:

    texture: QOpenGLTexture

    width: int

    height: int

    core_width: int

    core_height: int

    border_px: int

    has_mipmaps: bool

    approx_bytes: int


@dataclass(slots=True)
class _SkyViewMilkyWayPreparedTileCacheEntry:

    upload_image: QImage

    width: int

    height: int

    core_width: int

    core_height: int

    border_px: int

    source_format: str

    upload_format: str

    approx_bytes: int

    neighbor_tile_ids: tuple[str, ...]

    prepare_total_seconds: float

    file_read_seconds: float

    decode_seconds: float

    convert_seconds: float

    padding_seconds: float


@dataclass(slots=True)
class _SkyViewMilkyWayDecodedTileCacheEntry:

    image: QImage

    width: int

    height: int

    source_format: str

    approx_bytes: int


@dataclass(slots=True)
class _SkyViewMilkyWayPreparedTileFutureResult:

    cache_key: tuple[object, ...]

    entry: _SkyViewMilkyWayPreparedTileCacheEntry | None

    decode_success_count: int

    decode_failure_count: int

    decode_failure_path: str

    decode_failure_reason: str

    file_read_seconds: float

    decode_seconds: float

    convert_seconds: float

    padding_seconds: float

    prepare_total_seconds: float


@dataclass(slots=True)
class _SkyViewMilkyWayPreparedTileWorkerMetrics:

    decode_success_count: int = 0

    decode_failure_count: int = 0

    decode_failure_path: str = "none"

    decode_failure_reason: str = "none"

    file_read_seconds: float = 0.0

    decode_seconds: float = 0.0

    convert_seconds: float = 0.0

    padding_seconds: float = 0.0


@dataclass(slots=True)
class _SkyViewMilkyWayBaseUploadFutureResult:

    cache_key: tuple[object, ...]

    upload_image: QImage

    prepare_seconds: float



_MILKY_WAY_PREPARED_TILE_EXECUTOR = ThreadPoolExecutor(max_workers=2, thread_name_prefix="sky-view-milky-way-tile")


_MILKY_WAY_BASE_UPLOAD_EXECUTOR = ThreadPoolExecutor(max_workers=1, thread_name_prefix="sky-view-milky-way-base")


class OpenGLMilkyWayLayer:


    _TILE_TEXTURE_BORDER_PX = 1

    _RUNTIME_ALPHA_MASK_MAX_PIXELS = 12 * 1024 * 1024

    _PREPARED_TILE_FUTURE_SUBMIT_LIMIT_PER_FRAME = 4

    _PREPARED_TILE_FUTURE_HARVEST_LIMIT_PER_FRAME = 4

    _PREPARED_TILE_FUTURE_MAX_PENDING = 16

    _GL_ACTIVE_TEXTURE = 0x84E0
    _GL_ALWAYS = 0x0207
    _GL_BLEND = 0x0BE2
    _GL_BLEND_DST = 0x0BE0
    _GL_BLEND_SRC = 0x0BE1
    _GL_CURRENT_PROGRAM = 0x8B8D
    _GL_DEPTH_TEST = 0x0B71
    _GL_EQUAL = 0x0202
    _GL_KEEP = 0x1E00
    _GL_LINEAR = 0x2601
    _GL_LINEAR_MIPMAP_LINEAR = 0x2703
    _GL_NO_ERROR = 0
    _GL_NEAREST = 0x2600
    _GL_ONE_MINUS_SRC_ALPHA = 0x0303
    _GL_SCISSOR_TEST = 0x0C11
    _GL_SRC_ALPHA = 0x0302
    _GL_REPLACE = 0x1E01
    _GL_STENCIL_BUFFER_BIT = 0x00000400
    _GL_STENCIL_TEST = 0x0B90
    _GL_TEXTURE0 = 0x84C0
    _GL_TEXTURE_2D = 0x0DE1
    _GL_TEXTURE_BASE_LEVEL = 0x813C
    _GL_TEXTURE_BINDING_2D = 0x8069
    _GL_TEXTURE_HEIGHT = 0x1001
    _GL_TEXTURE_INTERNAL_FORMAT = 0x1003
    _GL_TEXTURE_MAG_FILTER = 0x2800
    _GL_TEXTURE_MAX_LEVEL = 0x813D
    _GL_TEXTURE_MIN_FILTER = 0x2801
    _GL_TEXTURE_WIDTH = 0x1000
    _GL_TEXTURE_WRAP_S = 0x2802
    _GL_TEXTURE_WRAP_T = 0x2803
    _GL_TRIANGLES = 0x0004
    _GL_UNPACK_ALIGNMENT = 0x0CF5
    _GL_VIEWPORT = 0x0BA2

    _VERTEX_SHADER_SOURCE = """
#version 120

varying vec2 v_texture_uv;

void main() {
    v_texture_uv = gl_MultiTexCoord0.xy;
    gl_Position = vec4(gl_Vertex.xy, 0.0, 1.0);
}
"""

    _FRAGMENT_SHADER_SOURCE = """
#version 120

uniform vec4 u_viewport_rect;
uniform vec2 u_viewport_size_px;
uniform float u_device_pixel_ratio;
uniform vec3 u_equatorial_forward;
uniform vec3 u_equatorial_up;
uniform vec3 u_equatorial_right;
uniform float u_half_horizontal_fov_rad;
uniform float u_half_vertical_fov_rad;
uniform float u_field_width_deg;
uniform float u_opacity;
uniform float u_brightness;
uniform float u_contrast;
uniform float u_saturation;
uniform float u_black_point;
uniform float u_gamma;
uniform float u_flip_horizontal;
uniform float u_texture_mode;
uniform float u_alpha_mode;
uniform float u_alpha_mask_black;
uniform float u_alpha_mask_white;
uniform float u_debug_output_mode;
uniform float u_debug_sample_mode;
uniform float u_debug_uv_mode;
uniform vec4 u_tile_uv_bounds;
uniform vec2 u_tile_dec_bounds_deg;
uniform float u_tile_include_max_v;
uniform vec3 u_tile_debug_color;
uniform vec3 u_tile_debug_exact_id;
uniform vec2 u_tile_texture_size;
uniform vec2 u_tile_texture_core_size;
uniform vec2 u_tile_texture_border_px;
uniform float u_debug_override_enabled;
uniform vec2 u_debug_override_local_uv;
uniform vec2 u_debug_override_global_uv;
uniform vec3 u_debug_override_raw_rgb;
uniform vec3 u_debug_override_toned_rgb;
uniform float u_debug_override_alpha;
uniform vec3 u_debug_override_tile_id;
uniform sampler2D u_texture;

varying vec2 v_texture_uv;

const float PI = 3.14159265358979323846;
const float TAU = 6.28318530717958647692;
const float CONTRAST_PIVOT = 0.28;

vec3 apply_tone_controls(vec3 rgb) {
    vec3 normalized = clamp(rgb, 0.0, 1.0);
    float black_point = clamp(u_black_point, 0.0, 0.95);
    normalized = max(normalized - vec3(black_point), vec3(0.0)) / max(1.0 - black_point, 0.001);
    normalized *= max(u_brightness, 0.0);
    normalized = normalized / (1.0 + normalized);
    normalized = (normalized - vec3(CONTRAST_PIVOT)) * max(u_contrast, 0.0) + vec3(CONTRAST_PIVOT);
    float luminance = dot(normalized, vec3(0.2126, 0.7152, 0.0722));
    normalized = mix(vec3(luminance), normalized, max(u_saturation, 0.0));
    normalized = clamp(normalized, 0.0, 1.0);
    float gamma_value = max(u_gamma, 0.05);
    return pow(normalized, vec3(1.0 / gamma_value));
}

vec3 compute_equatorial_ray() {
    float logical_x = u_viewport_rect.x + (gl_FragCoord.x / u_device_pixel_ratio);
    float logical_y = u_viewport_rect.y + ((u_viewport_size_px.y - gl_FragCoord.y) / u_device_pixel_ratio);
    float center_x = u_viewport_rect.x + (u_viewport_rect.z * 0.5);
    float center_y = u_viewport_rect.y + (u_viewport_rect.w * 0.5);
    float half_width = max(u_viewport_rect.z * 0.5, 1.0e-6);
    float half_height = max(u_viewport_rect.w * 0.5, 1.0e-6);
    float x_normalized = (logical_x - center_x) / half_width;
    float y_normalized = (center_y - logical_y) / half_height;
    float x_rad = x_normalized * u_half_horizontal_fov_rad;
    float y_rad = y_normalized * u_half_vertical_fov_rad;
    float angular_distance = length(vec2(x_rad, y_rad));
    if (angular_distance <= 1.0e-9) {
        return u_equatorial_forward;
    }
    float tangent_scale = sin(angular_distance) / angular_distance;
    float forward_scale = cos(angular_distance);
    return (u_equatorial_forward * forward_scale) + ((u_equatorial_right * x_rad) + (u_equatorial_up * y_rad)) * tangent_scale;
}

void compute_global_texture_uv(out vec2 texture_uv, out float dec_deg) {
    vec3 equatorial_ray = normalize(compute_equatorial_ray());
    float ra_fraction = mod(atan(equatorial_ray.y, equatorial_ray.x) + TAU, TAU) / TAU;
    float texture_u;
    if (u_flip_horizontal >= 0.5) {
        texture_u = mod(0.5 + ra_fraction, 1.0);
    } else {
        texture_u = mod(0.5 - ra_fraction, 1.0);
    }
    float texture_v = 0.5 - (asin(clamp(equatorial_ray.z, -1.0, 1.0)) / PI);
    texture_uv = vec2(texture_u, clamp(texture_v, 0.0, 1.0));
    dec_deg = asin(clamp(equatorial_ray.z, -1.0, 1.0)) * (180.0 / PI);
}

vec2 clamp_sample_uv(vec2 sample_uv) {
    return clamp(sample_uv, vec2(0.0), vec2(1.0));
}

vec2 resolved_tile_texture_size() {
    return max(u_tile_texture_size, vec2(1.0));
}

vec2 padded_tile_sample_uv(vec2 sample_uv) {
    vec2 texture_size = resolved_tile_texture_size();
    vec2 core_size = max(u_tile_texture_core_size, vec2(1.0));
    vec2 border_px = max(u_tile_texture_border_px, vec2(0.0));
    vec2 core_coord = clamp_sample_uv(sample_uv) * core_size;
    return clamp((core_coord + border_px) / texture_size, vec2(0.0), vec2(1.0));
}

vec2 texel_center_uv(vec2 texel_coord) {
    vec2 texture_size = resolved_tile_texture_size();
    vec2 clamped_coord = clamp(texel_coord, vec2(0.0), texture_size - vec2(1.0));
    return (clamped_coord + vec2(0.5)) / texture_size;
}

vec4 sample_tile_texture_nearest_center(vec2 sample_uv) {
    vec2 texture_size = resolved_tile_texture_size();
    vec2 texel_coord = floor(clamp_sample_uv(sample_uv) * texture_size);
    return texture2D(u_texture, texel_center_uv(texel_coord));
}

vec4 sample_tile_texture_manual_bilinear(vec2 sample_uv) {
    vec2 texture_size = resolved_tile_texture_size();
    vec2 scaled_coord = clamp_sample_uv(sample_uv) * texture_size - vec2(0.5);
    vec2 texel_floor = floor(scaled_coord);
    vec2 texel_fraction = scaled_coord - texel_floor;
    vec2 texel00 = clamp(texel_floor, vec2(0.0), texture_size - vec2(1.0));
    vec2 texel10 = clamp(texel00 + vec2(1.0, 0.0), vec2(0.0), texture_size - vec2(1.0));
    vec2 texel01 = clamp(texel00 + vec2(0.0, 1.0), vec2(0.0), texture_size - vec2(1.0));
    vec2 texel11 = clamp(texel00 + vec2(1.0, 1.0), vec2(0.0), texture_size - vec2(1.0));
    vec4 sample00 = texture2D(u_texture, texel_center_uv(texel00));
    vec4 sample10 = texture2D(u_texture, texel_center_uv(texel10));
    vec4 sample01 = texture2D(u_texture, texel_center_uv(texel01));
    vec4 sample11 = texture2D(u_texture, texel_center_uv(texel11));
    vec4 mix_x0 = mix(sample00, sample10, clamp(texel_fraction.x, 0.0, 1.0));
    vec4 mix_x1 = mix(sample01, sample11, clamp(texel_fraction.x, 0.0, 1.0));
    return mix(mix_x0, mix_x1, clamp(texel_fraction.y, 0.0, 1.0));
}

vec4 sample_resolved_tile_texture(vec2 sample_uv) {
    vec2 resolved_uv = padded_tile_sample_uv(sample_uv);
    if (u_debug_sample_mode >= 4.5) {
        return sample_tile_texture_manual_bilinear(resolved_uv);
    }
    if (u_debug_sample_mode >= 3.5) {
        return sample_tile_texture_nearest_center(resolved_uv);
    }
    return texture2D(u_texture, resolved_uv);
}

vec2 encode_u16(float value) {
    float scaled = floor(clamp(value, 0.0, 1.0) * 65535.0 + 0.5);
    float high_byte = floor(scaled / 256.0);
    float low_byte = scaled - (high_byte * 256.0);
    return vec2(high_byte, low_byte) / 255.0;
}

vec4 pack_vec2_u16(vec2 value) {
    vec2 x_bytes = encode_u16(value.x);
    vec2 y_bytes = encode_u16(value.y);
    return vec4(x_bytes.x, x_bytes.y, y_bytes.x, y_bytes.y);
}

vec4 pack_vec3_u16_high(vec3 value) {
    return vec4(encode_u16(value.x).x, encode_u16(value.y).x, encode_u16(value.z).x, 1.0);
}

vec4 pack_vec3_u16_low(vec3 value) {
    return vec4(encode_u16(value.x).y, encode_u16(value.y).y, encode_u16(value.z).y, 1.0);
}

vec4 pack_vec4_u16_high(vec4 value) {
    return vec4(encode_u16(value.x).x, encode_u16(value.y).x, encode_u16(value.z).x, encode_u16(value.w).x);
}

vec4 pack_vec4_u16_low(vec4 value) {
    return vec4(encode_u16(value.x).y, encode_u16(value.y).y, encode_u16(value.z).y, encode_u16(value.w).y);
}

vec4 sample_source_color(out vec2 sampled_uv) {
    if (u_texture_mode >= 1.5) {
        vec2 global_uv;
        float dec_deg;
        compute_global_texture_uv(global_uv, dec_deg);
        float tile_global_u = global_uv.x;
        if (u_flip_horizontal >= 0.5) {
            tile_global_u = mod(1.0 - tile_global_u, 1.0);
        }
        bool inside_u = tile_global_u >= u_tile_uv_bounds.x && tile_global_u < u_tile_uv_bounds.y;
        bool inside_v = dec_deg >= u_tile_dec_bounds_deg.x && (
            dec_deg < u_tile_dec_bounds_deg.y || (u_tile_include_max_v >= 0.5 && dec_deg <= u_tile_dec_bounds_deg.y)
        );
        if (!(inside_u && inside_v)) {
            discard;
        }
        sampled_uv = vec2(
            (tile_global_u - u_tile_uv_bounds.x) / max(u_tile_uv_bounds.y - u_tile_uv_bounds.x, 1.0e-6),
            (u_tile_dec_bounds_deg.y - dec_deg) / max(u_tile_dec_bounds_deg.y - u_tile_dec_bounds_deg.x, 1.0e-6)
        );
        return sample_resolved_tile_texture(sampled_uv);
    }
    if (u_texture_mode >= 0.5) {
        sampled_uv = clamp_sample_uv(v_texture_uv);
        return sample_resolved_tile_texture(sampled_uv);
    }
    float dec_deg;
    compute_global_texture_uv(sampled_uv, dec_deg);
    return texture2D(u_texture, clamp_sample_uv(sampled_uv));
}

vec3 tile_debug_color() {
    return clamp(u_tile_debug_color, vec3(0.0), vec3(1.0));
}

float structure_preserving_alpha(vec4 source_color, vec3 rgb) {
    float white_point = max(u_alpha_mask_black + 0.001, u_alpha_mask_white);
    float derived_mask = smoothstep(u_alpha_mask_black, white_point, max(max(rgb.r, rgb.g), rgb.b));
    float source_mask = clamp(source_color.a, 0.0, 1.0);
    float mask_value = source_mask < 0.999 ? source_mask : derived_mask;
    return u_opacity * mask_value;
}

float luminance_debug_alpha(vec3 rgb) {
    float luminance = dot(rgb, vec3(0.2126, 0.7152, 0.0722));
    float zoom_fade = clamp((u_field_width_deg - 8.0) / 30.0, 0.0, 1.0);
    float alpha_strength = pow(clamp((luminance - 0.018) * 3.1, 0.0, 1.0), 0.9);
    return min(alpha_strength * zoom_fade, 150.0 / 255.0) * u_opacity;
}

void main() {
    vec2 sampled_uv = vec2(0.0);
    vec4 source_color = sample_source_color(sampled_uv);
    vec2 global_uv = vec2(0.0);
    float dec_deg = 0.0;
    compute_global_texture_uv(global_uv, dec_deg);
    vec3 rgb = apply_tone_controls(source_color.rgb);
    float alpha = u_alpha_mode >= 0.5 ? luminance_debug_alpha(rgb) : structure_preserving_alpha(source_color, rgb);
    vec2 debug_local_uv = clamp(sampled_uv, vec2(0.0), vec2(1.0));
    vec2 debug_global_uv = clamp(global_uv, vec2(0.0), vec2(1.0));
    vec3 debug_raw_rgb = clamp(source_color.rgb, vec3(0.0), vec3(1.0));
    vec3 debug_toned_rgb = clamp(rgb, vec3(0.0), vec3(1.0));
    float debug_alpha = clamp(alpha, 0.0, 1.0);
    vec3 debug_tile_id = clamp(u_tile_debug_exact_id, vec3(0.0), vec3(1.0));
    if (u_debug_override_enabled >= 0.5) {
        debug_local_uv = clamp(u_debug_override_local_uv, vec2(0.0), vec2(1.0));
        debug_global_uv = clamp(u_debug_override_global_uv, vec2(0.0), vec2(1.0));
        debug_raw_rgb = clamp(u_debug_override_raw_rgb, vec3(0.0), vec3(1.0));
        debug_toned_rgb = clamp(u_debug_override_toned_rgb, vec3(0.0), vec3(1.0));
        debug_alpha = clamp(u_debug_override_alpha, 0.0, 1.0);
        debug_tile_id = clamp(u_debug_override_tile_id, vec3(0.0), vec3(1.0));
    }
    vec4 final_preblend = vec4(debug_toned_rgb, debug_alpha);
    if (u_debug_output_mode >= 0.5 && u_debug_output_mode < 1.5) {
        gl_FragColor = vec4(tile_debug_color(), 1.0);
        return;
    }
    if (u_debug_output_mode >= 1.5 && u_debug_output_mode < 2.5) {
        gl_FragColor = vec4(clamp(sampled_uv.x, 0.0, 1.0), clamp(sampled_uv.y, 0.0, 1.0), 0.25, 1.0);
        return;
    }
    if (u_debug_output_mode >= 2.5 && u_debug_output_mode < 3.5) {
        gl_FragColor = vec4(clamp(global_uv.x, 0.0, 1.0), clamp(global_uv.y, 0.0, 1.0), 0.25, 1.0);
        return;
    }
    if (u_debug_output_mode >= 3.5 && u_debug_output_mode < 4.5) {
        gl_FragColor = vec4(clamp(source_color.rgb, vec3(0.0), vec3(1.0)), 1.0);
        return;
    }
    if (u_debug_output_mode >= 4.5 && u_debug_output_mode < 5.5) {
        gl_FragColor = vec4(debug_raw_rgb, debug_alpha);
        return;
    }
    if (u_debug_output_mode >= 5.5 && u_debug_output_mode < 6.5) {
        gl_FragColor = vec4(clamp(rgb, vec3(0.0), vec3(1.0)), 1.0);
        return;
    }
    if (u_debug_output_mode >= 6.5 && u_debug_output_mode < 7.5) {
        gl_FragColor = vec4(vec3(clamp(alpha, 0.0, 1.0)), 1.0);
        return;
    }
    if (u_debug_output_mode >= 7.5 && u_debug_output_mode < 8.5) {
        gl_FragColor = vec4(1.0, 1.0, 1.0, 1.0);
        return;
    }
    if (u_debug_output_mode >= 8.5 && u_debug_output_mode < 9.5) {
        gl_FragColor = vec4(debug_tile_id, 1.0);
        return;
    }
    if (u_debug_output_mode >= 9.5 && u_debug_output_mode < 10.5) {
        gl_FragColor = pack_vec2_u16(debug_local_uv);
        return;
    }
    if (u_debug_output_mode >= 10.5 && u_debug_output_mode < 11.5) {
        gl_FragColor = pack_vec2_u16(debug_global_uv);
        return;
    }
    if (u_debug_output_mode >= 11.5 && u_debug_output_mode < 12.5) {
        gl_FragColor = pack_vec3_u16_high(debug_raw_rgb);
        return;
    }
    if (u_debug_output_mode >= 12.5 && u_debug_output_mode < 13.5) {
        gl_FragColor = pack_vec3_u16_low(debug_raw_rgb);
        return;
    }
    if (u_debug_output_mode >= 13.5 && u_debug_output_mode < 14.5) {
        gl_FragColor = pack_vec3_u16_high(debug_toned_rgb);
        return;
    }
    if (u_debug_output_mode >= 14.5 && u_debug_output_mode < 15.5) {
        gl_FragColor = pack_vec3_u16_low(debug_toned_rgb);
        return;
    }
    if (u_debug_output_mode >= 15.5 && u_debug_output_mode < 16.5) {
        vec2 alpha_bytes = encode_u16(debug_alpha);
        gl_FragColor = vec4(alpha_bytes.x, alpha_bytes.y, 1.0, 1.0);
        return;
    }
    if (u_debug_output_mode >= 16.5 && u_debug_output_mode < 17.5) {
        gl_FragColor = pack_vec4_u16_high(final_preblend);
        return;
    }
    if (u_debug_output_mode >= 17.5 && u_debug_output_mode < 18.5) {
        gl_FragColor = pack_vec4_u16_low(final_preblend);
        return;
    }
    if (u_debug_uv_mode >= 0.5 && u_texture_mode >= 0.5) {
        gl_FragColor = vec4(clamp(sampled_uv.x, 0.0, 1.0), clamp(sampled_uv.y, 0.0, 1.0), 0.25, 1.0);
        return;
    }
    gl_FragColor = final_preblend;
}
"""

    def __init__(self) -> None:

        self._program: QOpenGLShaderProgram | None = None

        self._uniform_locations: dict[str, int] = {}

        self._texture: QOpenGLTexture | None = None

        self._texture_cache_key: tuple[object, ...] | None = None

        self._tile_textures: OrderedDict[tuple[object, ...], _SkyViewMilkyWayTextureCacheEntry] = OrderedDict()

        self._tile_cache_total_bytes = 0

        self._prepared_tiles: OrderedDict[tuple[object, ...], _SkyViewMilkyWayPreparedTileCacheEntry] = OrderedDict()

        self._prepared_tile_cache_total_bytes = 0

        self._decoded_tiles: OrderedDict[tuple[object, ...], _SkyViewMilkyWayDecodedTileCacheEntry] = OrderedDict()

        self._decoded_tile_cache_total_bytes = 0

        self._prepared_tile_futures: dict[tuple[object, ...], Future[_SkyViewMilkyWayPreparedTileFutureResult]] = {}

        self._prepared_tile_future_errors: dict[tuple[object, ...], str] = {}

        self._base_upload_future: Future[_SkyViewMilkyWayBaseUploadFutureResult] | None = None

        self._base_upload_future_key: tuple[object, ...] | None = None

        self.last_program_init_seconds = 0.0

        self.last_texture_upload_seconds = 0.0

        self.last_tile_upload_seconds = 0.0

        self.last_tile_cache_lookup_seconds = 0.0

        self.last_tile_file_read_seconds = 0.0

        self.last_tile_decode_seconds = 0.0

        self.last_tile_decode_success_count = 0

        self.last_tile_decode_failure_count = 0

        self.last_tile_decode_failure_path = "none"

        self.last_tile_decode_failure_reason = "none"

        self.last_tile_convert_seconds = 0.0

        self.last_tile_border_copy_seconds = 0.0

        self.last_tile_padding_seconds = 0.0

        self.last_tile_prepare_total_seconds = 0.0

        self.last_draw_seconds = 0.0

        self.last_program_initialized = False

        self.last_texture_uploaded = False

        self.last_texture_has_mipmaps = False

        self.texture_width = 0

        self.texture_height = 0

        self.last_asset_mode = "single_global"

        self.last_tile_renderer = "shader"

        self.last_alpha_mode = "structure_preserving"

        self.last_tile_sample_mode = "normal"

        self.last_visible_tile_count = 0

        self.last_requested_tile_count = 0

        self.last_empty_tile_skip_count = 0

        self.last_resident_tile_count = 0

        self.last_drawn_tile_count = 0

        self.last_tile_cache_hits = 0

        self.last_tile_cache_misses = 0

        self.last_tile_textures_uploaded = 0

        self.last_tile_cache_entries = 0

        self.last_tile_cache_bytes = 0

        self.last_cpu_tile_cache_entries = 0

        self.last_cpu_tile_cache_bytes = 0

        self.last_tile_cpu_cache_hits = 0

        self.last_tile_cpu_cache_misses = 0

        self.last_prepared_tile_future_submits = 0

        self.last_prepared_tile_future_harvested = 0

        self.last_prepared_tile_future_pending = 0

        self.last_prepared_tile_future_errors = 0

        self.last_prepared_tile_future_file_read_seconds = 0.0

        self.last_prepared_tile_future_decode_seconds = 0.0

        self.last_prepared_tile_future_convert_seconds = 0.0

        self.last_prepared_tile_future_padding_seconds = 0.0

        self.last_prepared_tile_future_prepare_total_seconds = 0.0

        self.last_base_upload_future_submitted = False

        self.last_base_upload_future_harvested = False

        self.last_base_upload_future_pending = False

        self.last_base_upload_prepare_seconds = 0.0

        self.last_requested_tile_ids: tuple[str, ...] = ()

        self.last_resident_tile_ids: tuple[str, ...] = ()

        self.last_missing_tile_ids: tuple[str, ...] = ()

        self.last_deferred_tile_ids: tuple[str, ...] = ()

        self.last_base_drawn = False

        self.last_base_source = "none"

        self.last_all_requested_tiles_resident = False

        self.last_base_skipped_reason = "none"

        self.last_tile_texture_state: dict[str, object] | None = None

        self.last_draw_call_count = 0

        self.last_base_draw_call_count = 0

        self.last_tile_draw_call_count = 0

        self.last_support_mask_draw_call_count = 0

        self.last_support_mask_used = False

        self.last_texture_bind_count = 0

        self.last_tile_texture_bind_count = 0

        self.last_texture_eviction_count = 0

        self.last_prepared_tile_eviction_count = 0

        self.last_decoded_tile_eviction_count = 0

    def initialize(self, functions: QOpenGLFunctions_2_0 | None) -> bool:

        self.last_program_init_seconds = 0.0

        self.last_program_initialized = False

        if functions is None:

            return False

        if self._program is not None:

            return True

        benchmark_recorder = get_benchmark_recorder() if BENCHMARK_ENABLED else None

        benchmark_token = benchmark_recorder.start_section("milky_way.gl_program_init") if benchmark_recorder is not None else None

        init_start = perf_counter()

        program = QOpenGLShaderProgram()

        if not program.addShaderFromSourceCode(QOpenGLShader.ShaderTypeBit.Vertex, self._VERTEX_SHADER_SOURCE):

            compile_log = program.log().strip()

            raise RuntimeError(
                f"Failed to compile Sky View GL Milky Way vertex shader: {compile_log or 'unknown shader compile error'}"
            )

        if not program.addShaderFromSourceCode(QOpenGLShader.ShaderTypeBit.Fragment, self._FRAGMENT_SHADER_SOURCE):

            compile_log = program.log().strip()

            raise RuntimeError(
                f"Failed to compile Sky View GL Milky Way fragment shader: {compile_log or 'unknown shader compile error'}"
            )

        if not program.link():

            link_log = program.log().strip()

            raise RuntimeError(
                f"Failed to link Sky View GL Milky Way shader program: {link_log or 'unknown shader link error'}"
            )

        uniform_locations = {
            name: int(program.uniformLocation(name))
            for name in (
                "u_viewport_rect",
                "u_viewport_size_px",
                "u_device_pixel_ratio",
                "u_equatorial_forward",
                "u_equatorial_up",
                "u_equatorial_right",
                "u_half_horizontal_fov_rad",
                "u_half_vertical_fov_rad",
                "u_field_width_deg",
                "u_opacity",
                "u_brightness",
                "u_contrast",
                "u_saturation",
                "u_black_point",
                "u_gamma",
                "u_flip_horizontal",
                "u_texture_mode",
                "u_alpha_mode",
                "u_alpha_mask_black",
                "u_alpha_mask_white",
                "u_debug_output_mode",
                "u_debug_sample_mode",
                "u_debug_uv_mode",
                "u_tile_uv_bounds",
                "u_tile_dec_bounds_deg",
                "u_tile_include_max_v",
                "u_tile_debug_color",
                "u_tile_debug_exact_id",
                "u_tile_texture_size",
                "u_tile_texture_core_size",
                "u_tile_texture_border_px",
                "u_debug_override_enabled",
                "u_debug_override_local_uv",
                "u_debug_override_global_uv",
                "u_debug_override_raw_rgb",
                "u_debug_override_toned_rgb",
                "u_debug_override_alpha",
                "u_debug_override_tile_id",
                "u_texture",
            )
        }

        missing_uniforms = sorted(name for name, location in uniform_locations.items() if location < 0)

        if missing_uniforms:

            raise RuntimeError("Sky View GL Milky Way shader is missing uniforms: " + ", ".join(missing_uniforms))

        self._program = program

        self._uniform_locations = uniform_locations

        self.last_program_init_seconds = perf_counter() - init_start

        self.last_program_initialized = True

        if benchmark_recorder is not None:

            benchmark_recorder.stop_section(benchmark_token, metadata={"initialized": True})

        return True

    def reset_frame_diagnostics(self) -> None:

        self.last_program_init_seconds = 0.0

        self.last_texture_upload_seconds = 0.0

        self.last_tile_upload_seconds = 0.0

        self.last_tile_cache_lookup_seconds = 0.0

        self.last_tile_file_read_seconds = 0.0

        self.last_tile_decode_seconds = 0.0

        self.last_tile_decode_success_count = 0

        self.last_tile_decode_failure_count = 0

        self.last_tile_decode_failure_path = "none"

        self.last_tile_decode_failure_reason = "none"

        self.last_tile_convert_seconds = 0.0

        self.last_tile_border_copy_seconds = 0.0

        self.last_tile_padding_seconds = 0.0

        self.last_tile_prepare_total_seconds = 0.0

        self.last_draw_seconds = 0.0

        self.last_program_initialized = False

        self.last_texture_uploaded = False

        self.last_visible_tile_count = 0

        self.last_requested_tile_count = 0

        self.last_resident_tile_count = 0

        self.last_drawn_tile_count = 0

        self.last_tile_cache_hits = 0

        self.last_tile_cache_misses = 0

        self.last_tile_textures_uploaded = 0

        self.last_tile_cache_entries = len(self._tile_textures)

        self.last_tile_cache_bytes = self._tile_cache_total_bytes

        self.last_cpu_tile_cache_entries = len(self._prepared_tiles)

        self.last_cpu_tile_cache_bytes = self._prepared_tile_cache_total_bytes

        self.last_tile_cpu_cache_hits = 0

        self.last_tile_cpu_cache_misses = 0

        self.last_prepared_tile_future_submits = 0

        self.last_prepared_tile_future_harvested = 0

        self.last_prepared_tile_future_pending = len(self._prepared_tile_futures)

        self.last_prepared_tile_future_errors = len(self._prepared_tile_future_errors)

        self.last_prepared_tile_future_file_read_seconds = 0.0

        self.last_prepared_tile_future_decode_seconds = 0.0

        self.last_prepared_tile_future_convert_seconds = 0.0

        self.last_prepared_tile_future_padding_seconds = 0.0

        self.last_prepared_tile_future_prepare_total_seconds = 0.0

        self.last_base_upload_future_submitted = False

        self.last_base_upload_future_harvested = False

        self.last_base_upload_future_pending = self._base_upload_future is not None

        self.last_base_upload_prepare_seconds = 0.0

        self.last_tile_renderer = "single_global"

        self.last_tile_sample_mode = "normal"

        self.last_requested_tile_ids = ()

        self.last_skipped_empty_tile_ids = ()

        self.last_resident_tile_ids = ()

        self.last_missing_tile_ids = ()

        self.last_deferred_tile_ids = ()

        self.last_base_drawn = False

        self.last_base_source = "none"

        self.last_all_requested_tiles_resident = False

        self.last_base_skipped_reason = "none"

        self.last_tile_texture_state = None

        self.last_draw_call_count = 0

        self.last_base_draw_call_count = 0

        self.last_tile_draw_call_count = 0

        self.last_support_mask_draw_call_count = 0

        self.last_support_mask_used = False

        self.last_texture_bind_count = 0

        self.last_tile_texture_bind_count = 0

        self.last_texture_eviction_count = 0

        self.last_prepared_tile_eviction_count = 0

        self.last_decoded_tile_eviction_count = 0

    def release(self) -> None:

        self._cancel_prepared_tile_futures()

        self._cancel_base_upload_future()

        self._destroy_tile_textures()

        self._destroy_prepared_tiles()

        self._destroy_decoded_tiles()

        texture = self._texture

        self._texture = None

        self._texture_cache_key = None

        self.last_texture_has_mipmaps = False

        self.texture_width = 0

        self.texture_height = 0

        if texture is not None:

            try:

                texture.release()

            except Exception:

                pass

            try:

                texture.destroy()

            except Exception:

                pass

        program = self._program

        self._program = None

        self._uniform_locations = {}

        if program is None:

            return

        try:

            program.release()

        except Exception:

            pass

        try:

            program.removeAllShaders()

        except Exception:

            pass

    def is_available(self, functions: QOpenGLFunctions_2_0 | None) -> bool:

        if functions is None:

            return False

        if self._program is None:

            return self.initialize(functions)

        return True

    def draw(
        self,
        functions: QOpenGLFunctions_2_0 | None,
        uniforms: SkyViewMilkyWayGlUniforms,
        source_image: QImage | None,
        texture_cache_key: tuple[object, ...] | None,
        *,
        asset_mode: str = "single_global",
        base_source: str = "none",
        tile_requests: tuple[SkyViewMilkyWayTileDrawRequest, ...] = (),
        tile_upload_limit: int = 2,
        tile_cache_budget_bytes: int = 256 * 1024 * 1024,
        prepared_tile_cache_budget_bytes: int = 512 * 1024 * 1024,
        debug_render_mode: str = "normal",
        debug_output_mode: str = "final",
        tile_sample_mode: str = "normal",
        debug_uv_enabled: bool = False,
        diagnostic_override: dict[str, object] | None = None,
        allow_blocking_tile_prepare: bool = False,
    ) -> bool:

        benchmark_recorder = get_benchmark_recorder() if BENCHMARK_ENABLED else None

        benchmark_draw_token = benchmark_recorder.start_section("milky_way.gl_draw") if benchmark_recorder is not None else None

        self.last_program_init_seconds = 0.0

        self.last_texture_upload_seconds = 0.0

        self.last_tile_upload_seconds = 0.0

        self.last_tile_cache_lookup_seconds = 0.0

        self.last_tile_file_read_seconds = 0.0

        self.last_tile_decode_seconds = 0.0

        self.last_tile_decode_success_count = 0

        self.last_tile_decode_failure_count = 0

        self.last_tile_decode_failure_path = "none"

        self.last_tile_decode_failure_reason = "none"

        self.last_tile_convert_seconds = 0.0

        self.last_tile_border_copy_seconds = 0.0

        self.last_tile_padding_seconds = 0.0

        self.last_tile_prepare_total_seconds = 0.0

        self.last_draw_seconds = 0.0

        self.last_program_initialized = False

        self.last_texture_uploaded = False

        self.last_asset_mode = str(asset_mode or "single_global")

        self.last_alpha_mode = str(uniforms.alpha_mode or "structure_preserving")

        self.last_tile_sample_mode = str(tile_sample_mode or "normal").strip().casefold()

        raw_tile_requests = tuple(tile_requests or ())

        skipped_empty_tile_requests = tuple(tile_request for tile_request in raw_tile_requests if tile_request.has_signal is False)

        resolved_tile_requests = tuple(tile_request for tile_request in raw_tile_requests if tile_request.has_signal is not False)

        self.last_tile_renderer = str(resolved_tile_requests[0].tile_renderer if resolved_tile_requests else "single_global")

        self.last_visible_tile_count = len(resolved_tile_requests)

        self.last_requested_tile_count = len(raw_tile_requests)

        self.last_empty_tile_skip_count = len(skipped_empty_tile_requests)

        self.last_resident_tile_count = 0

        self.last_drawn_tile_count = 0

        self.last_tile_cache_hits = 0

        self.last_tile_cache_misses = 0

        self.last_tile_textures_uploaded = 0

        self.last_tile_cache_entries = len(self._tile_textures)

        self.last_tile_cache_bytes = self._tile_cache_total_bytes

        self.last_cpu_tile_cache_entries = len(self._prepared_tiles)

        self.last_cpu_tile_cache_bytes = self._prepared_tile_cache_total_bytes

        self.last_tile_cpu_cache_hits = 0

        self.last_tile_cpu_cache_misses = 0

        self.last_prepared_tile_future_submits = 0

        self.last_prepared_tile_future_harvested = 0

        self.last_prepared_tile_future_pending = len(self._prepared_tile_futures)

        self.last_prepared_tile_future_errors = len(self._prepared_tile_future_errors)

        self.last_prepared_tile_future_file_read_seconds = 0.0

        self.last_prepared_tile_future_decode_seconds = 0.0

        self.last_prepared_tile_future_convert_seconds = 0.0

        self.last_prepared_tile_future_padding_seconds = 0.0

        self.last_prepared_tile_future_prepare_total_seconds = 0.0

        self.last_base_upload_future_submitted = False

        self.last_base_upload_future_harvested = False

        self.last_base_upload_future_pending = self._base_upload_future is not None

        self.last_base_upload_prepare_seconds = 0.0

        self.last_requested_tile_ids = tuple(self._tile_debug_id(tile_request) for tile_request in raw_tile_requests)

        self.last_skipped_empty_tile_ids = tuple(self._tile_debug_id(tile_request) for tile_request in skipped_empty_tile_requests)

        self.last_resident_tile_ids = ()

        self.last_missing_tile_ids = ()

        self.last_deferred_tile_ids = ()

        self.last_base_drawn = False

        self.last_base_source = str(base_source or "none")

        self.last_all_requested_tiles_resident = False

        self.last_base_skipped_reason = "none"

        self.last_tile_texture_state = None

        self.last_draw_call_count = 0

        self.last_base_draw_call_count = 0

        self.last_tile_draw_call_count = 0

        self.last_support_mask_draw_call_count = 0

        self.last_support_mask_used = False

        self.last_texture_bind_count = 0

        self.last_tile_texture_bind_count = 0

        self.last_texture_eviction_count = 0

        self.last_prepared_tile_eviction_count = 0

        self.last_decoded_tile_eviction_count = 0

        if functions is None:

            if benchmark_recorder is not None:

                benchmark_recorder.stop_section(benchmark_draw_token, metadata={"error": "missing_gl_functions"})

            raise RuntimeError("OpenGL functions are unavailable")

        base_available = source_image is not None and not source_image.isNull() and texture_cache_key is not None

        if not self.is_available(functions):

            if benchmark_recorder is not None:

                benchmark_recorder.stop_section(benchmark_draw_token, metadata={"available": False})

            return False

        if base_available:

            assert source_image is not None

            assert texture_cache_key is not None

            benchmark_stage_token = benchmark_recorder.start_section("milky_way.base_texture_prepare") if benchmark_recorder is not None else None

            self._ensure_texture(source_image, texture_cache_key)

            if benchmark_recorder is not None:

                benchmark_recorder.stop_section(benchmark_stage_token, metadata={"texture_uploaded": self.last_texture_uploaded})

        program = self._program

        if program is None:

            raise RuntimeError("Sky View GL Milky Way shader program is unavailable")

        benchmark_state_token = benchmark_recorder.start_section("milky_way.capture_previous_gl_state") if benchmark_recorder is not None else None

        previous_viewport = self._integer_values(functions, self._GL_VIEWPORT, 4)

        if previous_viewport is not None and (previous_viewport[2] <= 0 or previous_viewport[3] <= 0):

            previous_viewport = None

        previous_program = self._integer_values(functions, self._GL_CURRENT_PROGRAM, 1)

        previous_blend_src = self._integer_values(functions, self._GL_BLEND_SRC, 1)

        previous_blend_dst = self._integer_values(functions, self._GL_BLEND_DST, 1)

        previous_active_texture = self._integer_values(functions, self._GL_ACTIVE_TEXTURE, 1)

        previous_texture_binding: tuple[int, ...] | None = None

        if previous_active_texture is not None:

            try:

                getattr(functions, "glActiveTexture")(self._GL_TEXTURE0)

                previous_texture_binding = self._integer_values(functions, self._GL_TEXTURE_BINDING_2D, 1)

            finally:

                getattr(functions, "glActiveTexture")(int(previous_active_texture[0]))

        previous_enabled_states = {
            self._GL_BLEND: self._is_enabled(functions, self._GL_BLEND),
            self._GL_DEPTH_TEST: self._is_enabled(functions, self._GL_DEPTH_TEST),
            self._GL_SCISSOR_TEST: self._is_enabled(functions, self._GL_SCISSOR_TEST),
            self._GL_STENCIL_TEST: self._is_enabled(functions, self._GL_STENCIL_TEST),
            self._GL_TEXTURE_2D: self._is_enabled(functions, self._GL_TEXTURE_2D),
        }

        if benchmark_recorder is not None:

            benchmark_recorder.stop_section(benchmark_state_token)

        program_bound = False

        texture_bound = False

        normalized_render_mode = str(debug_render_mode or "normal").strip().casefold()

        draw_base = normalized_render_mode not in {"tiles_only", "tiles_over_flat", "normal_without_base"}

        draw_tiles = normalized_render_mode != "base_only"

        force_base_draw = normalized_render_mode == "normal_with_base_forced"

        resolved_debug_output_mode = str(debug_output_mode or "final").strip().casefold()

        resolved_tile_sample_mode = str(tile_sample_mode or "normal").strip().casefold()

        exact_debug_output = self._debug_output_requires_exact_write(resolved_debug_output_mode, debug_uv_enabled=debug_uv_enabled)

        resident_tile_ids: list[str] = []

        missing_tile_ids: list[str] = []

        deferred_tile_ids: list[str] = []

        resident_tile_draws: list[tuple[SkyViewMilkyWayTileDrawRequest, _SkyViewMilkyWayTextureCacheEntry]] = []

        protected_tile_cache_keys = {tuple(tile_request.texture_cache_key) for tile_request in resolved_tile_requests}

        try:

            benchmark_shader_token = benchmark_recorder.start_section("milky_way.shader_setup") if benchmark_recorder is not None else None

            self._check_gl_error(functions, "before Milky Way draw")

            functions.glViewport(0, 0, int(uniforms.viewport_width_px), int(uniforms.viewport_height_px))

            if exact_debug_output:

                functions.glDisable(self._GL_BLEND)

            else:

                functions.glEnable(self._GL_BLEND)

                functions.glBlendFunc(self._GL_SRC_ALPHA, self._GL_ONE_MINUS_SRC_ALPHA)

            functions.glDisable(self._GL_DEPTH_TEST)

            functions.glDisable(self._GL_SCISSOR_TEST)

            functions.glDisable(self._GL_STENCIL_TEST)

            getattr(functions, "glActiveTexture")(self._GL_TEXTURE0)

            functions.glEnable(self._GL_TEXTURE_2D)

            texture = self._texture

            if texture is None:

                texture_bound = False

            if not program.bind():

                bind_log = program.log().strip()

                raise RuntimeError(
                    f"Failed to bind Sky View GL Milky Way shader program: {bind_log or 'unknown shader bind error'}"
                )

            program_bound = True

            self._set_uniform_values(functions, uniforms)

            self._set_debug_override_uniforms(functions, diagnostic_override)

            functions.glUniform1f(
                self._uniform_locations["u_debug_output_mode"],
                float(self._debug_output_mode_value(resolved_debug_output_mode, debug_uv_enabled=debug_uv_enabled)),
            )

            functions.glUniform1f(
                self._uniform_locations["u_debug_sample_mode"],
                float(self._debug_sample_mode_value(resolved_tile_sample_mode)),
            )

            functions.glUniform1f(self._uniform_locations["u_debug_uv_mode"], 1.0 if debug_uv_enabled else 0.0)

            if benchmark_recorder is not None:

                benchmark_recorder.stop_section(

                    benchmark_shader_token,

                    metadata={

                        "debug_output_mode": resolved_debug_output_mode,

                        "tile_sample_mode": resolved_tile_sample_mode,

                        "exact_debug_output": exact_debug_output,

                    },

                )

            benchmark_tile_cache_token = benchmark_recorder.start_section(

                "milky_way.tile_cache_lookup_total",

                metadata={"requested_tiles": len(resolved_tile_requests), "upload_limit": int(tile_upload_limit)},

            ) if benchmark_recorder is not None else None

            if draw_tiles and self.last_asset_mode == "tiled_manifest" and resolved_tile_requests:

                self._harvest_prepared_tile_futures(
                    cache_budget_bytes=prepared_tile_cache_budget_bytes,
                    max_completed=self._PREPARED_TILE_FUTURE_HARVEST_LIMIT_PER_FRAME,
                )

                remaining_uploads = max(0, int(tile_upload_limit))

                for tile_request in resolved_tile_requests:

                    tile_id = self._tile_debug_id(tile_request)

                    tile_texture_entry, cache_hit, uploaded = self._resolve_tile_texture(
                        tile_request,
                        remaining_uploads=remaining_uploads,
                        cache_budget_bytes=tile_cache_budget_bytes,
                        prepared_cache_budget_bytes=prepared_tile_cache_budget_bytes,
                        protected_cache_keys=protected_tile_cache_keys,
                        allow_blocking_tile_prepare=bool(allow_blocking_tile_prepare),
                    )

                    if not cache_hit:

                        self.last_tile_cache_misses += 1

                    if cache_hit:

                        self.last_tile_cache_hits += 1

                    if uploaded:

                        remaining_uploads = max(0, remaining_uploads - 1)

                        self.last_tile_textures_uploaded += 1

                    if tile_texture_entry is None:

                        if tile_request.is_missing:

                            missing_tile_ids.append(tile_id)

                        else:

                            deferred_tile_ids.append(tile_id)

                        continue

                    self.last_resident_tile_count += 1

                    resident_tile_ids.append(tile_id)

                    resident_tile_draws.append((tile_request, tile_texture_entry))

            if benchmark_recorder is not None:

                benchmark_recorder.stop_section(

                    benchmark_tile_cache_token,

                    metadata={

                        "requested_tiles": len(resolved_tile_requests),

                        "resident_tiles": len(resident_tile_draws),

                        "cache_hits": self.last_tile_cache_hits,

                        "cache_misses": self.last_tile_cache_misses,

                        "uploads": self.last_tile_textures_uploaded,

                        "prepared_future_submits": self.last_prepared_tile_future_submits,

                        "prepared_future_harvested": self.last_prepared_tile_future_harvested,

                        "prepared_future_pending": len(self._prepared_tile_futures),

                    },

                )

            all_requested_tiles_resident = (
                self.last_asset_mode == "tiled_manifest"
                and bool(resolved_tile_requests)
                and len(resident_tile_draws) == len(resolved_tile_requests)
            )

            self.last_all_requested_tiles_resident = all_requested_tiles_resident

            draw_start = perf_counter()

            did_draw = False

            base_tile_exclusion_active = False

            suppress_resident_manifest_base = (
                draw_base
                and self.last_asset_mode == "tiled_manifest"
                and all_requested_tiles_resident
                and normalized_render_mode == "normal"
            )

            exclude_resident_tiles_from_base = (
                draw_base
                and self.last_asset_mode == "tiled_manifest"
                and bool(resident_tile_draws)
                and normalized_render_mode == "normal"
                and not exact_debug_output
            )

            benchmark_support_token = benchmark_recorder.start_section(

                "milky_way.support_mask_prepare",

                metadata={"candidate_tiles": len(resident_tile_draws), "enabled": bool(exclude_resident_tiles_from_base)},

            ) if benchmark_recorder is not None else None

            if exclude_resident_tiles_from_base:

                try:

                    functions.glEnable(self._GL_STENCIL_TEST)

                    functions.glClearStencil(0)

                    functions.glClear(self._GL_STENCIL_BUFFER_BIT)

                    functions.glStencilMask(0xFF)

                    functions.glStencilFunc(self._GL_ALWAYS, 1, 0xFF)

                    functions.glStencilOp(self._GL_KEEP, self._GL_KEEP, self._GL_REPLACE)

                    functions.glColorMask(False, False, False, False)

                    for tile_request, tile_texture_entry in resident_tile_draws:

                        tile_texture_entry.texture.bind(0)

                        self.last_texture_bind_count += 1

                        self.last_tile_texture_bind_count += 1

                        self._apply_tile_texture_sample_mode(
                            tile_texture_entry.texture,
                            sample_mode=resolved_tile_sample_mode,
                            has_mipmaps=tile_texture_entry.has_mipmaps,
                        )

                        self._set_tile_uniforms(functions, tile_request, tile_texture_entry=tile_texture_entry)

                        self._draw_tile_request(functions, tile_request)

                        self.last_support_mask_draw_call_count += 1

                        self.last_draw_call_count += 1

                    functions.glColorMask(True, True, True, True)

                    functions.glStencilMask(0x00)

                    functions.glStencilFunc(self._GL_EQUAL, 0, 0xFF)

                    functions.glStencilOp(self._GL_KEEP, self._GL_KEEP, self._GL_KEEP)

                    base_tile_exclusion_active = True

                except Exception:

                    try:

                        functions.glColorMask(True, True, True, True)

                        functions.glStencilMask(0xFF)

                        functions.glDisable(self._GL_STENCIL_TEST)

                    except Exception:

                        pass

                    base_tile_exclusion_active = False

            self.last_support_mask_used = bool(base_tile_exclusion_active)

            if benchmark_recorder is not None:

                benchmark_recorder.stop_section(

                    benchmark_support_token,

                    metadata={

                        "used": bool(base_tile_exclusion_active),

                        "draw_calls": self.last_support_mask_draw_call_count,

                    },

                )

            if base_tile_exclusion_active:

                suppress_resident_manifest_base = False

            if not draw_base:

                self.last_base_skipped_reason = "debug_render_mode"

            elif not base_available or texture is None:

                self.last_base_skipped_reason = "no_base_texture"

            elif suppress_resident_manifest_base:

                self.last_base_skipped_reason = "all_tiles_resident"

            benchmark_base_token = benchmark_recorder.start_section(

                "milky_way.base_texture_draw",

                metadata={"draw_base": bool(draw_base), "base_available": bool(base_available)},

            ) if benchmark_recorder is not None else None

            if draw_base and not suppress_resident_manifest_base and base_available and texture is not None:

                texture.bind(0)

                self.last_texture_bind_count += 1

                texture_bound = True

                self._set_texture_mode(functions, local_uv=False)

                self._draw_fullscreen_triangle(functions)

                self.last_base_draw_call_count += 1

                self.last_draw_call_count += 1

                did_draw = True

                self.last_base_drawn = True

                self.last_base_skipped_reason = "none"

            if benchmark_recorder is not None:

                benchmark_recorder.stop_section(

                    benchmark_base_token,

                    metadata={

                        "drawn": self.last_base_drawn,

                        "skipped_reason": self.last_base_skipped_reason,

                        "draw_calls": self.last_base_draw_call_count,

                    },

                )

            if base_tile_exclusion_active:

                functions.glStencilMask(0xFF)

                functions.glDisable(self._GL_STENCIL_TEST)

            benchmark_tiles_token = benchmark_recorder.start_section(

                "milky_way.high_res_tiled_layer_draw",

                metadata={"resident_tiles": len(resident_tile_draws)},

            ) if benchmark_recorder is not None else None

            for tile_request, tile_texture_entry in resident_tile_draws:

                tile_texture_entry.texture.bind(0)

                self.last_texture_bind_count += 1

                self.last_tile_texture_bind_count += 1

                self._apply_tile_texture_sample_mode(
                    tile_texture_entry.texture,
                    sample_mode=resolved_tile_sample_mode,
                    has_mipmaps=tile_texture_entry.has_mipmaps,
                )

                texture_bound = True

                self._set_tile_uniforms(functions, tile_request, tile_texture_entry=tile_texture_entry)

                if self.last_tile_texture_state is None:

                    self.last_tile_texture_state = self._capture_tile_texture_state(
                        functions,
                        tile_request=tile_request,
                        tile_texture_entry=tile_texture_entry,
                        sample_mode=resolved_tile_sample_mode,
                    )

                self._draw_tile_request(functions, tile_request)

                self.last_tile_draw_call_count += 1

                self.last_draw_call_count += 1

                did_draw = True

                self.last_drawn_tile_count += 1

            if benchmark_recorder is not None:

                benchmark_recorder.stop_section(

                    benchmark_tiles_token,

                    metadata={

                        "resident_tiles": len(resident_tile_draws),

                        "drawn_tiles": self.last_drawn_tile_count,

                        "draw_calls": self.last_tile_draw_call_count,

                        "texture_binds": self.last_tile_texture_bind_count,

                    },

                )

            self.last_draw_seconds = perf_counter() - draw_start

            self.last_tile_cache_entries = len(self._tile_textures)

            self.last_tile_cache_bytes = self._tile_cache_total_bytes

            self.last_cpu_tile_cache_entries = len(self._prepared_tiles)

            self.last_cpu_tile_cache_bytes = self._prepared_tile_cache_total_bytes

            self.last_resident_tile_ids = tuple(resident_tile_ids)

            self.last_missing_tile_ids = tuple(missing_tile_ids)

            self.last_deferred_tile_ids = tuple(deferred_tile_ids)

            self._check_gl_error(functions, "after Milky Way draw")

            return did_draw

        finally:

            if program_bound:

                program.release()

            if texture_bound and self._texture is not None:

                self._texture.release()

            if previous_program is not None:

                try:

                    getattr(functions, "glUseProgram")(int(previous_program[0]))

                except Exception:

                    pass

            if previous_active_texture is not None:

                try:

                    getattr(functions, "glActiveTexture")(self._GL_TEXTURE0)

                    if previous_texture_binding is not None:

                        getattr(functions, "glBindTexture")(self._GL_TEXTURE_2D, int(previous_texture_binding[0]))

                    getattr(functions, "glActiveTexture")(int(previous_active_texture[0]))

                except Exception:

                    pass

            if previous_blend_src is not None and previous_blend_dst is not None:

                try:

                    functions.glBlendFunc(int(previous_blend_src[0]), int(previous_blend_dst[0]))

                except Exception:

                    pass

            try:

                functions.glColorMask(True, True, True, True)

                functions.glStencilMask(0xFF)

            except Exception:

                pass

            self._restore_enabled_states(functions, previous_enabled_states)

            if previous_viewport is not None:

                functions.glViewport(
                    int(previous_viewport[0]),
                    int(previous_viewport[1]),
                    int(previous_viewport[2]),
                    int(previous_viewport[3]),
                )

            if benchmark_recorder is not None:

                benchmark_recorder.stop_section(

                    benchmark_draw_token,

                    metadata={

                        "asset_mode": self.last_asset_mode,

                        "base_source": self.last_base_source,

                        "base_drawn": self.last_base_drawn,

                        "base_skipped_reason": self.last_base_skipped_reason,

                        "requested_tiles": self.last_requested_tile_count,

                        "visible_tiles": self.last_visible_tile_count,

                        "resident_tiles": self.last_resident_tile_count,

                        "drawn_tiles": self.last_drawn_tile_count,

                        "tile_cache_hits": self.last_tile_cache_hits,

                        "tile_cache_misses": self.last_tile_cache_misses,

                        "tile_uploads": self.last_tile_textures_uploaded,

                        "cpu_tile_cache_hits": self.last_tile_cpu_cache_hits,

                        "cpu_tile_cache_misses": self.last_tile_cpu_cache_misses,

                        "prepared_future_submits": self.last_prepared_tile_future_submits,

                        "prepared_future_harvested": self.last_prepared_tile_future_harvested,

                        "prepared_future_pending": len(self._prepared_tile_futures),

                        "prepared_future_errors": len(self._prepared_tile_future_errors),

                        "base_upload_future_submitted": self.last_base_upload_future_submitted,

                        "base_upload_future_harvested": self.last_base_upload_future_harvested,

                        "base_upload_future_pending": self._base_upload_future is not None,

                        "draw_calls": self.last_draw_call_count,

                        "base_draw_calls": self.last_base_draw_call_count,

                        "tile_draw_calls": self.last_tile_draw_call_count,

                        "support_mask_draw_calls": self.last_support_mask_draw_call_count,

                        "support_mask_used": self.last_support_mask_used,

                        "texture_binds": self.last_texture_bind_count,

                        "tile_texture_binds": self.last_tile_texture_bind_count,

                        "texture_evictions": self.last_texture_eviction_count,

                        "prepared_tile_evictions": self.last_prepared_tile_eviction_count,

                        "decoded_tile_evictions": self.last_decoded_tile_eviction_count,

                    },

                )

    def _ensure_texture(self, source_image: QImage, texture_cache_key: tuple[object, ...]) -> None:

        if self._texture is not None and self._texture_cache_key == texture_cache_key and self._texture.isCreated():

            return

        ready_image = self._harvest_base_upload_future(texture_cache_key)

        if ready_image is None:

            self._schedule_base_upload_prepare(source_image, texture_cache_key)

            return

        benchmark_recorder = get_benchmark_recorder() if BENCHMARK_ENABLED else None

        benchmark_token = benchmark_recorder.start_section("milky_way.base_texture_upload") if benchmark_recorder is not None else None

        if self._texture is not None:

            try:

                self._texture.release()

            except Exception:

                pass

            try:

                self._texture.destroy()

            except Exception:

                pass

            self._texture = None

        upload_start = perf_counter()

        texture, width, height, has_mipmaps = self._create_texture(ready_image, repeat_s=True, upload_ready=True)

        self.texture_width = width

        self.texture_height = height

        self.last_texture_has_mipmaps = has_mipmaps

        self._texture = texture

        self._texture_cache_key = texture_cache_key

        self.last_texture_upload_seconds = perf_counter() - upload_start

        self.last_texture_uploaded = True

        if benchmark_recorder is not None:

            benchmark_recorder.stop_section(

                benchmark_token,

                metadata={"width": width, "height": height, "has_mipmaps": has_mipmaps},

            )

    @staticmethod
    def _prepare_base_upload_image_worker(
        source_image: QImage,
        texture_cache_key: tuple[object, ...],
    ) -> _SkyViewMilkyWayBaseUploadFutureResult:

        prepare_start = perf_counter()

        upload_image, _status = OpenGLMilkyWayLayer._prepare_milky_way_upload_image(source_image)

        if not upload_image.isNull():

            upload_image = upload_image.mirrored(False, True)

        return _SkyViewMilkyWayBaseUploadFutureResult(
            cache_key=tuple(texture_cache_key),
            upload_image=upload_image,
            prepare_seconds=perf_counter() - prepare_start,
        )

    def _schedule_base_upload_prepare(self, source_image: QImage, texture_cache_key: tuple[object, ...]) -> None:

        cache_key = tuple(texture_cache_key)

        if self._base_upload_future is not None:

            if self._base_upload_future_key == cache_key:

                self.last_base_upload_future_pending = not self._base_upload_future.done()

                return

            if not self._base_upload_future.done():

                self._base_upload_future.cancel()

        self._base_upload_future_key = cache_key

        self._base_upload_future = _MILKY_WAY_BASE_UPLOAD_EXECUTOR.submit(
            OpenGLMilkyWayLayer._prepare_base_upload_image_worker,
            QImage(source_image),
            cache_key,
        )
        self.last_base_upload_future_submitted = True
        self.last_base_upload_future_pending = True

    def _harvest_base_upload_future(self, texture_cache_key: tuple[object, ...]) -> QImage | None:

        future = self._base_upload_future

        cache_key = tuple(texture_cache_key)

        if future is None or self._base_upload_future_key != cache_key:

            return None

        if not future.done():

            self.last_base_upload_future_pending = True

            return None

        self._base_upload_future = None

        self._base_upload_future_key = None

        try:

            result = future.result()

        except Exception:

            self.last_base_upload_future_pending = False

            return None

        self.last_base_upload_future_harvested = True
        self.last_base_upload_future_pending = False
        self.last_base_upload_prepare_seconds += result.prepare_seconds

        if result.cache_key != cache_key or result.upload_image.isNull():

            return None

        return result.upload_image

    def _cancel_base_upload_future(self) -> None:

        future = self._base_upload_future

        if future is not None and not future.done():

            future.cancel()
        self._base_upload_future = None
        self._base_upload_future_key = None

    def _create_texture(
        self,
        source_image: QImage,
        *,
        repeat_s: bool,
        upload_ready: bool = False,
    ) -> tuple[QOpenGLTexture, int, int, bool]:

        upload_image = QImage(source_image) if upload_ready else self._milky_way_upload_image(source_image).mirrored(False, True)

        texture = QOpenGLTexture(upload_image)

        texture.setMagnificationFilter(QOpenGLTexture.Filter.Linear)

        has_mipmaps = False

        try:

            texture.generateMipMaps()

            texture.setMinificationFilter(QOpenGLTexture.Filter.LinearMipMapLinear)

            has_mipmaps = True

        except Exception:

            texture.setMinificationFilter(QOpenGLTexture.Filter.Linear)

        texture.setWrapMode(
            QOpenGLTexture.CoordinateDirection.DirectionS,
            QOpenGLTexture.WrapMode.Repeat if repeat_s else QOpenGLTexture.WrapMode.ClampToEdge,
        )

        texture.setWrapMode(QOpenGLTexture.CoordinateDirection.DirectionT, QOpenGLTexture.WrapMode.ClampToEdge)

        return texture, int(upload_image.width()), int(upload_image.height()), has_mipmaps

    @classmethod
    def _milky_way_upload_image(cls, source_image: QImage) -> QImage:

        benchmark_recorder = get_benchmark_recorder() if BENCHMARK_ENABLED else None

        benchmark_token = benchmark_recorder.start_section("milky_way.alpha_mask_prepare") if benchmark_recorder is not None else None

        if source_image.isNull():

            if benchmark_recorder is not None:

                benchmark_recorder.stop_section(benchmark_token, metadata={"status": "null_image"})

            return QImage(source_image)

        result, status = cls._prepare_milky_way_upload_image(source_image)

        if benchmark_recorder is not None:

            benchmark_recorder.stop_section(

                benchmark_token,

                metadata={"status": status, "width": result.width(), "height": result.height()},

            )

        return result

    @classmethod
    def _prepare_milky_way_upload_image(cls, source_image: QImage) -> tuple[QImage, str]:

        if source_image.isNull():

            return QImage(source_image), "null_image"

        if source_image.hasAlphaChannel():

            rgba_image = source_image.convertToFormat(QImage.Format.Format_RGBA8888)

            rgba_pixels = cls._qimage_pixels(rgba_image, channels=4)

            if np.any(rgba_pixels[..., 3] < np.uint8(255)):

                return rgba_image, "source_alpha"

            rgb_pixels = rgba_pixels[..., :3]

        else:

            rgb_image = source_image.convertToFormat(QImage.Format.Format_RGB888)

            if int(rgb_image.width()) * int(rgb_image.height()) > cls._RUNTIME_ALPHA_MASK_MAX_PIXELS:

                return rgb_image, "skipped_large_rgb"

            rgb_pixels = cls._qimage_pixels(rgb_image, channels=3)

        masked_pixels = apply_milky_way_alpha_mask(rgb_pixels)

        result = cls._rgba_qimage_from_pixels(masked_pixels)

        return result, "mask_applied"

    @staticmethod
    def _qimage_pixels(image: QImage, *, channels: int) -> np.ndarray:

        if image.isNull() or int(image.width()) <= 0 or int(image.height()) <= 0:

            return np.zeros((0, 0, int(channels)), dtype=np.uint8)

        raw = np.frombuffer(image.constBits(), dtype=np.uint8)

        rows = raw.reshape((int(image.height()), int(image.bytesPerLine())))

        return rows[:, : int(image.width()) * int(channels)].reshape((int(image.height()), int(image.width()), int(channels)))

    @staticmethod
    def _rgba_qimage_from_pixels(rgba_pixels: np.ndarray) -> QImage:

        rgba = np.ascontiguousarray(rgba_pixels, dtype=np.uint8)

        if rgba.ndim != 3 or rgba.shape[2] != 4:

            raise ValueError(f"Expected RGBA pixels, got shape {rgba.shape!r}")

        height, width = int(rgba.shape[0]), int(rgba.shape[1])

        if width <= 0 or height <= 0:

            return QImage()

        return QImage(rgba.data, width, height, int(rgba.strides[0]), QImage.Format.Format_RGBA8888).copy()

    def _load_tile_image_with_neighbor_border(self, tile_request: SkyViewMilkyWayTileDrawRequest) -> QImage:

        padded_image, _source_format, _neighbor_tile_ids = self._load_tile_image_with_neighbor_border_details(tile_request)

        return padded_image

    def _load_tile_image_with_neighbor_border_details(
        self,
        tile_request: SkyViewMilkyWayTileDrawRequest,
        *,
        decoded_cache_budget_bytes: int | None = None,
    ) -> tuple[QImage, str, tuple[str, ...]]:

        resolved_budget = max(1, int(decoded_cache_budget_bytes or (64 * 1024 * 1024)))

        tile_image, source_format = self._resolve_decoded_tile_image(
            tile_request.texture_path,
            cache_budget_bytes=resolved_budget,
        )

        if tile_image.isNull():

            return tile_image, "unknown", ()

        border_px = self._TILE_TEXTURE_BORDER_PX

        if border_px <= 0 or (tile_request.padded_tile and int(tile_request.gutter_pixels) == border_px):

            return tile_image, source_format, ()

        tile_width = int(tile_image.width())

        tile_height = int(tile_image.height())

        padded_format = QImage.Format.Format_RGBA8888 if tile_image.hasAlphaChannel() else QImage.Format.Format_RGB888

        padded_image = QImage(
            tile_width + (border_px * 2),
            tile_height + (border_px * 2),
            padded_format,
        )

        padded_image.fill(0)

        neighbor_cache: dict[tuple[int, int], QImage | None] = {}

        loaded_neighbor_tile_ids: list[str] = []

        def neighbor(dx: int, dy: int) -> QImage | None:

            key = (int(dx), int(dy))

            if key not in neighbor_cache:

                neighbor_image = self._load_neighbor_tile_image(
                    tile_request,
                    dx=dx,
                    dy=dy,
                    expected_width=tile_width,
                    expected_height=tile_height,
                    decoded_cache_budget_bytes=resolved_budget,
                )

                if neighbor_image is not None:

                    loaded_neighbor_tile_ids.append(self._neighbor_tile_id(tile_request, dx=dx, dy=dy))

                neighbor_cache[key] = neighbor_image

            return neighbor_cache[key]

        copy_start = perf_counter()

        painter = QPainter(padded_image)

        try:

            painter.drawImage(border_px, border_px, tile_image)

            self._draw_tile_border_strip(
                painter,
                source_image=neighbor(-1, 0) or tile_image,
                fallback_image=tile_image,
                source_rect=QRect(tile_width - border_px, 0, border_px, tile_height) if neighbor(-1, 0) is not None else QRect(0, 0, border_px, tile_height),
                target_rect=QRect(0, border_px, border_px, tile_height),
            )

            self._draw_tile_border_strip(
                painter,
                source_image=neighbor(1, 0) or tile_image,
                fallback_image=tile_image,
                source_rect=QRect(0, 0, border_px, tile_height) if neighbor(1, 0) is not None else QRect(tile_width - border_px, 0, border_px, tile_height),
                target_rect=QRect(tile_width + border_px, border_px, border_px, tile_height),
            )

            self._draw_tile_border_strip(
                painter,
                source_image=neighbor(0, -1) or tile_image,
                fallback_image=tile_image,
                source_rect=QRect(0, tile_height - border_px, tile_width, border_px) if neighbor(0, -1) is not None else QRect(0, 0, tile_width, border_px),
                target_rect=QRect(border_px, 0, tile_width, border_px),
            )

            self._draw_tile_border_strip(
                painter,
                source_image=neighbor(0, 1) or tile_image,
                fallback_image=tile_image,
                source_rect=QRect(0, 0, tile_width, border_px) if neighbor(0, 1) is not None else QRect(0, tile_height - border_px, tile_width, border_px),
                target_rect=QRect(border_px, tile_height + border_px, tile_width, border_px),
            )

            self._draw_tile_border_strip(
                painter,
                source_image=neighbor(-1, -1) or tile_image,
                fallback_image=tile_image,
                source_rect=QRect(tile_width - border_px, tile_height - border_px, border_px, border_px) if neighbor(-1, -1) is not None else QRect(0, 0, border_px, border_px),
                target_rect=QRect(0, 0, border_px, border_px),
            )

            self._draw_tile_border_strip(
                painter,
                source_image=neighbor(1, -1) or tile_image,
                fallback_image=tile_image,
                source_rect=QRect(0, tile_height - border_px, border_px, border_px) if neighbor(1, -1) is not None else QRect(tile_width - border_px, 0, border_px, border_px),
                target_rect=QRect(tile_width + border_px, 0, border_px, border_px),
            )

            self._draw_tile_border_strip(
                painter,
                source_image=neighbor(-1, 1) or tile_image,
                fallback_image=tile_image,
                source_rect=QRect(tile_width - border_px, 0, border_px, border_px) if neighbor(-1, 1) is not None else QRect(0, tile_height - border_px, border_px, border_px),
                target_rect=QRect(0, tile_height + border_px, border_px, border_px),
            )

            self._draw_tile_border_strip(
                painter,
                source_image=neighbor(1, 1) or tile_image,
                fallback_image=tile_image,
                source_rect=QRect(0, 0, border_px, border_px) if neighbor(1, 1) is not None else QRect(tile_width - border_px, tile_height - border_px, border_px, border_px),
                target_rect=QRect(tile_width + border_px, tile_height + border_px, border_px, border_px),
            )

        finally:

            painter.end()

        padding_seconds = perf_counter() - copy_start

        self.last_tile_border_copy_seconds += padding_seconds

        self.last_tile_padding_seconds += padding_seconds

        return padded_image, source_format, tuple(loaded_neighbor_tile_ids)

    @staticmethod
    def _read_image_file(image_path: Path) -> tuple[QImage, float, float]:

        benchmark_recorder = get_benchmark_recorder() if BENCHMARK_ENABLED else None

        benchmark_token = (

            benchmark_recorder.start_section("milky_way.tile_file_read_decode", metadata={"path": image_path.name})

            if benchmark_recorder is not None

            else None

        )

        read_start = perf_counter()

        try:

            image_bytes = image_path.read_bytes()

        except OSError:

            if benchmark_recorder is not None:

                benchmark_recorder.stop_section(benchmark_token, metadata={"status": "read-error"})

            return QImage(), 0.0, 0.0

        file_read_seconds = perf_counter() - read_start

        decode_start = perf_counter()

        image = QImage.fromData(image_bytes)

        decode_seconds = perf_counter() - decode_start

        if benchmark_recorder is not None:

            benchmark_recorder.stop_section(

                benchmark_token,

                metadata={

                    "status": "ok" if not image.isNull() else "decode-error",

                    "bytes": len(image_bytes),

                    "file_read_seconds": file_read_seconds,

                    "decode_seconds": decode_seconds,

                    "width": int(image.width()) if not image.isNull() else 0,

                    "height": int(image.height()) if not image.isNull() else 0,

                },

            )

        return image, file_read_seconds, decode_seconds

    @staticmethod
    def _draw_tile_border_strip(
        painter: QPainter,
        *,
        source_image: QImage,
        fallback_image: QImage,
        source_rect: QRect,
        target_rect: QRect,
    ) -> None:

        if source_image.isNull():

            source_image = fallback_image

        painter.drawImage(target_rect, source_image, source_rect)

    def _load_neighbor_tile_image(
        self,
        tile_request: SkyViewMilkyWayTileDrawRequest,
        *,
        dx: int,
        dy: int,
        expected_width: int,
        expected_height: int,
        decoded_cache_budget_bytes: int | None = None,
    ) -> QImage | None:

        neighbor_path = self._neighbor_tile_path(tile_request, dx=dx, dy=dy)

        if neighbor_path is None or not neighbor_path.is_file():

            return None

        neighbor_image, _source_format = self._resolve_decoded_tile_image(
            neighbor_path,
            cache_budget_bytes=max(1, int(decoded_cache_budget_bytes or (64 * 1024 * 1024))),
        )

        if neighbor_image.isNull():

            return None

        if int(neighbor_image.width()) != int(expected_width) or int(neighbor_image.height()) != int(expected_height):

            return None

        return neighbor_image

    @staticmethod
    def _neighbor_tile_path(tile_request: SkyViewMilkyWayTileDrawRequest, *, dx: int, dy: int) -> Path | None:

        tile_count_x, tile_count_y = tile_request.tile_grid_shape

        resolved_count_x = max(1, int(tile_count_x))

        resolved_count_y = max(1, int(tile_count_y))

        neighbor_y = int(tile_request.y_index) + int(dy)

        if neighbor_y < 0 or neighbor_y >= resolved_count_y:

            return None

        neighbor_x = (int(tile_request.x_index) + int(dx)) % resolved_count_x

        suffix = tile_request.texture_path.suffix or ".png"

        return tile_request.texture_path.parent / f"{neighbor_x}_{neighbor_y}{suffix}"

    @staticmethod
    def _neighbor_tile_id(tile_request: SkyViewMilkyWayTileDrawRequest, *, dx: int, dy: int) -> str:

        tile_count_x, tile_count_y = tile_request.tile_grid_shape

        resolved_count_x = max(1, int(tile_count_x))

        resolved_count_y = max(1, int(tile_count_y))

        neighbor_y = max(0, min(resolved_count_y - 1, int(tile_request.y_index) + int(dy)))

        neighbor_x = (int(tile_request.x_index) + int(dx)) % resolved_count_x

        return f"L{int(tile_request.level)}/{neighbor_x}/{neighbor_y}"

    @staticmethod
    def _image_format_name(image: QImage) -> str:

        try:

            format_value = image.format()

            return str(getattr(format_value, "name", format_value))

        except Exception:

            return "unknown"

    @staticmethod
    def _image_approx_bytes(image: QImage) -> int:

        try:

            return max(1, int(image.sizeInBytes()))

        except Exception:

            return max(1, int(image.bytesPerLine()) * int(image.height()))

    @staticmethod
    def _decoded_tile_cache_key(image_path: Path) -> tuple[object, ...]:

        try:

            tile_stat = image_path.stat()

            return (str(image_path.resolve()), int(tile_stat.st_mtime_ns), int(tile_stat.st_size))

        except OSError:

            return (str(image_path), "missing")

    def _resolve_decoded_tile_image(
        self,
        image_path: Path,
        *,
        cache_budget_bytes: int,
    ) -> tuple[QImage, str]:

        benchmark_recorder = get_benchmark_recorder() if BENCHMARK_ENABLED else None

        benchmark_token = (

            benchmark_recorder.start_section("milky_way.decoded_tile_cache", metadata={"path": image_path.name})

            if benchmark_recorder is not None

            else None

        )

        cache_key = self._decoded_tile_cache_key(image_path)

        cached_entry = self._decoded_tiles.get(cache_key)

        if cached_entry is not None:

            self._decoded_tiles.move_to_end(cache_key)

            if benchmark_recorder is not None:

                benchmark_recorder.stop_section(

                    benchmark_token,

                    metadata={"cache_status": "hit", "source_format": cached_entry.source_format},

                )

            return cached_entry.image, cached_entry.source_format

        image, file_read_seconds, decode_seconds = self._read_image_file(image_path)

        self.last_tile_file_read_seconds += file_read_seconds

        self.last_tile_decode_seconds += decode_seconds

        if image.isNull():

            self.last_tile_decode_failure_count += 1

            self.last_tile_decode_failure_path = str(image_path)

            self.last_tile_decode_failure_reason = qt_image_decode_failure_reason(image_path)

            if benchmark_recorder is not None:

                benchmark_recorder.stop_section(

                    benchmark_token,

                    metadata={

                        "cache_status": "miss",

                        "status": "decode-error",

                        "reason": self.last_tile_decode_failure_reason,

                    },

                )

            return image, "unknown"

        self.last_tile_decode_success_count += 1

        self.last_tile_decode_failure_path = "none"

        self.last_tile_decode_failure_reason = "none"

        source_format = self._image_format_name(image)

        convert_start = perf_counter()

        if image.hasAlphaChannel():

            image = image.convertToFormat(QImage.Format.Format_RGBA8888)

        else:

            image = image.convertToFormat(QImage.Format.Format_RGB888)

        self.last_tile_convert_seconds += perf_counter() - convert_start

        entry = _SkyViewMilkyWayDecodedTileCacheEntry(
            image=image,
            width=int(image.width()),
            height=int(image.height()),
            source_format=source_format,
            approx_bytes=self._image_approx_bytes(image),
        )

        self._decoded_tiles[cache_key] = entry

        self._decoded_tiles.move_to_end(cache_key)

        self._decoded_tile_cache_total_bytes += entry.approx_bytes

        self._evict_decoded_tiles_to_budget(cache_budget_bytes)

        if benchmark_recorder is not None:

            benchmark_recorder.stop_section(

                benchmark_token,

                metadata={

                    "cache_status": "miss",

                    "status": "ok",

                    "source_format": source_format,

                    "width": entry.width,

                    "height": entry.height,

                    "approx_bytes": entry.approx_bytes,

                },

            )

        return entry.image, entry.source_format

    @staticmethod
    def _core_image_size(tile_request: SkyViewMilkyWayTileDrawRequest, padded_image: QImage, border_px: int) -> tuple[int, int]:

        if tile_request.padded_tile and int(tile_request.gutter_pixels) == int(border_px):

            _content_x, _content_y, content_width, content_height = tile_request.content_region

            if int(content_width) > 0 and int(content_height) > 0:

                return int(content_width), int(content_height)

        return (
            max(1, int(padded_image.width()) - (int(border_px) * 2)),
            max(1, int(padded_image.height()) - (int(border_px) * 2)),
        )

    @staticmethod
    def _read_and_convert_tile_image_for_worker(
        image_path: Path,
        metrics: _SkyViewMilkyWayPreparedTileWorkerMetrics,
    ) -> tuple[QImage, str]:

        read_start = perf_counter()

        try:

            image_bytes = image_path.read_bytes()

        except OSError:

            metrics.decode_failure_count += 1
            metrics.decode_failure_path = str(image_path)
            metrics.decode_failure_reason = "read-error"
            return QImage(), "unknown"

        metrics.file_read_seconds += perf_counter() - read_start

        decode_start = perf_counter()

        image = QImage.fromData(image_bytes)

        metrics.decode_seconds += perf_counter() - decode_start

        if image.isNull():

            metrics.decode_failure_count += 1
            metrics.decode_failure_path = str(image_path)
            metrics.decode_failure_reason = qt_image_decode_failure_reason(image_path)
            return image, "unknown"

        metrics.decode_success_count += 1
        metrics.decode_failure_path = "none"
        metrics.decode_failure_reason = "none"

        source_format = OpenGLMilkyWayLayer._image_format_name(image)

        convert_start = perf_counter()

        if image.hasAlphaChannel():

            image = image.convertToFormat(QImage.Format.Format_RGBA8888)

        else:

            image = image.convertToFormat(QImage.Format.Format_RGB888)

        metrics.convert_seconds += perf_counter() - convert_start

        return image, source_format

    @staticmethod
    def _load_worker_neighbor_tile_image(
        tile_request: SkyViewMilkyWayTileDrawRequest,
        *,
        dx: int,
        dy: int,
        expected_width: int,
        expected_height: int,
        metrics: _SkyViewMilkyWayPreparedTileWorkerMetrics,
    ) -> QImage | None:

        neighbor_path = OpenGLMilkyWayLayer._neighbor_tile_path(tile_request, dx=dx, dy=dy)

        if neighbor_path is None or not neighbor_path.is_file():

            return None

        neighbor_image, _source_format = OpenGLMilkyWayLayer._read_and_convert_tile_image_for_worker(neighbor_path, metrics)

        if neighbor_image.isNull():

            return None

        if int(neighbor_image.width()) != int(expected_width) or int(neighbor_image.height()) != int(expected_height):

            return None

        return neighbor_image

    @staticmethod
    def _prepare_tile_for_upload_worker(
        tile_request: SkyViewMilkyWayTileDrawRequest,
    ) -> _SkyViewMilkyWayPreparedTileFutureResult:

        cache_key = tuple(tile_request.texture_cache_key)

        metrics = _SkyViewMilkyWayPreparedTileWorkerMetrics()

        prepare_start = perf_counter()

        tile_image, source_format = OpenGLMilkyWayLayer._read_and_convert_tile_image_for_worker(
            tile_request.texture_path,
            metrics,
        )

        if tile_image.isNull():

            return _SkyViewMilkyWayPreparedTileFutureResult(
                cache_key=cache_key,
                entry=None,
                decode_success_count=metrics.decode_success_count,
                decode_failure_count=metrics.decode_failure_count,
                decode_failure_path=metrics.decode_failure_path,
                decode_failure_reason=metrics.decode_failure_reason,
                file_read_seconds=metrics.file_read_seconds,
                decode_seconds=metrics.decode_seconds,
                convert_seconds=metrics.convert_seconds,
                padding_seconds=metrics.padding_seconds,
                prepare_total_seconds=perf_counter() - prepare_start,
            )

        border_px = OpenGLMilkyWayLayer._TILE_TEXTURE_BORDER_PX
        neighbor_tile_ids: tuple[str, ...] = ()

        if border_px <= 0 or (tile_request.padded_tile and int(tile_request.gutter_pixels) == border_px):

            padded_image = tile_image

        else:

            tile_width = int(tile_image.width())
            tile_height = int(tile_image.height())
            padded_format = QImage.Format.Format_RGBA8888 if tile_image.hasAlphaChannel() else QImage.Format.Format_RGB888
            padded_image = QImage(tile_width + (border_px * 2), tile_height + (border_px * 2), padded_format)
            padded_image.fill(0)
            neighbor_cache: dict[tuple[int, int], QImage | None] = {}
            loaded_neighbor_tile_ids: list[str] = []

            def neighbor(dx: int, dy: int) -> QImage | None:

                key = (int(dx), int(dy))
                if key not in neighbor_cache:
                    neighbor_image = OpenGLMilkyWayLayer._load_worker_neighbor_tile_image(
                        tile_request,
                        dx=dx,
                        dy=dy,
                        expected_width=tile_width,
                        expected_height=tile_height,
                        metrics=metrics,
                    )
                    if neighbor_image is not None:
                        loaded_neighbor_tile_ids.append(OpenGLMilkyWayLayer._neighbor_tile_id(tile_request, dx=dx, dy=dy))
                    neighbor_cache[key] = neighbor_image
                return neighbor_cache[key]

            copy_start = perf_counter()
            painter = QPainter(padded_image)
            try:
                painter.drawImage(border_px, border_px, tile_image)
                OpenGLMilkyWayLayer._draw_tile_border_strip(
                    painter,
                    source_image=neighbor(-1, 0) or tile_image,
                    fallback_image=tile_image,
                    source_rect=QRect(tile_width - border_px, 0, border_px, tile_height) if neighbor(-1, 0) is not None else QRect(0, 0, border_px, tile_height),
                    target_rect=QRect(0, border_px, border_px, tile_height),
                )
                OpenGLMilkyWayLayer._draw_tile_border_strip(
                    painter,
                    source_image=neighbor(1, 0) or tile_image,
                    fallback_image=tile_image,
                    source_rect=QRect(0, 0, border_px, tile_height) if neighbor(1, 0) is not None else QRect(tile_width - border_px, 0, border_px, tile_height),
                    target_rect=QRect(tile_width + border_px, border_px, border_px, tile_height),
                )
                OpenGLMilkyWayLayer._draw_tile_border_strip(
                    painter,
                    source_image=neighbor(0, -1) or tile_image,
                    fallback_image=tile_image,
                    source_rect=QRect(0, tile_height - border_px, tile_width, border_px) if neighbor(0, -1) is not None else QRect(0, 0, tile_width, border_px),
                    target_rect=QRect(border_px, 0, tile_width, border_px),
                )
                OpenGLMilkyWayLayer._draw_tile_border_strip(
                    painter,
                    source_image=neighbor(0, 1) or tile_image,
                    fallback_image=tile_image,
                    source_rect=QRect(0, 0, tile_width, border_px) if neighbor(0, 1) is not None else QRect(0, tile_height - border_px, tile_width, border_px),
                    target_rect=QRect(border_px, tile_height + border_px, tile_width, border_px),
                )
                OpenGLMilkyWayLayer._draw_tile_border_strip(
                    painter,
                    source_image=neighbor(-1, -1) or tile_image,
                    fallback_image=tile_image,
                    source_rect=QRect(tile_width - border_px, tile_height - border_px, border_px, border_px) if neighbor(-1, -1) is not None else QRect(0, 0, border_px, border_px),
                    target_rect=QRect(0, 0, border_px, border_px),
                )
                OpenGLMilkyWayLayer._draw_tile_border_strip(
                    painter,
                    source_image=neighbor(1, -1) or tile_image,
                    fallback_image=tile_image,
                    source_rect=QRect(0, tile_height - border_px, border_px, border_px) if neighbor(1, -1) is not None else QRect(tile_width - border_px, 0, border_px, border_px),
                    target_rect=QRect(tile_width + border_px, 0, border_px, border_px),
                )
                OpenGLMilkyWayLayer._draw_tile_border_strip(
                    painter,
                    source_image=neighbor(-1, 1) or tile_image,
                    fallback_image=tile_image,
                    source_rect=QRect(tile_width - border_px, 0, border_px, border_px) if neighbor(-1, 1) is not None else QRect(0, tile_height - border_px, border_px, border_px),
                    target_rect=QRect(0, tile_height + border_px, border_px, border_px),
                )
                OpenGLMilkyWayLayer._draw_tile_border_strip(
                    painter,
                    source_image=neighbor(1, 1) or tile_image,
                    fallback_image=tile_image,
                    source_rect=QRect(0, 0, border_px, border_px) if neighbor(1, 1) is not None else QRect(tile_width - border_px, tile_height - border_px, border_px, border_px),
                    target_rect=QRect(tile_width + border_px, tile_height + border_px, border_px, border_px),
                )
            finally:
                painter.end()
            metrics.padding_seconds += perf_counter() - copy_start
            neighbor_tile_ids = tuple(loaded_neighbor_tile_ids)

        intermediate_image = QImage(padded_image).mirrored(False, True)
        upload_image, _upload_status = OpenGLMilkyWayLayer._prepare_milky_way_upload_image(intermediate_image)
        if not upload_image.isNull():
            upload_image = upload_image.mirrored(False, True)

        core_width, core_height = OpenGLMilkyWayLayer._core_image_size(
            tile_request,
            padded_image,
            border_px,
        )
        prepare_total_seconds = perf_counter() - prepare_start
        entry = _SkyViewMilkyWayPreparedTileCacheEntry(
            upload_image=upload_image,
            width=int(upload_image.width()),
            height=int(upload_image.height()),
            core_width=core_width,
            core_height=core_height,
            border_px=border_px,
            source_format=source_format,
            upload_format=OpenGLMilkyWayLayer._image_format_name(upload_image),
            approx_bytes=OpenGLMilkyWayLayer._image_approx_bytes(upload_image),
            neighbor_tile_ids=neighbor_tile_ids,
            prepare_total_seconds=prepare_total_seconds,
            file_read_seconds=metrics.file_read_seconds,
            decode_seconds=metrics.decode_seconds,
            convert_seconds=metrics.convert_seconds,
            padding_seconds=metrics.padding_seconds,
        )
        return _SkyViewMilkyWayPreparedTileFutureResult(
            cache_key=cache_key,
            entry=entry,
            decode_success_count=metrics.decode_success_count,
            decode_failure_count=metrics.decode_failure_count,
            decode_failure_path=metrics.decode_failure_path,
            decode_failure_reason=metrics.decode_failure_reason,
            file_read_seconds=metrics.file_read_seconds,
            decode_seconds=metrics.decode_seconds,
            convert_seconds=metrics.convert_seconds,
            padding_seconds=metrics.padding_seconds,
            prepare_total_seconds=prepare_total_seconds,
        )

    def _store_prepared_tile_future_result(
        self,
        result: _SkyViewMilkyWayPreparedTileFutureResult,
        *,
        cache_budget_bytes: int,
        count_as_render_path_work: bool,
    ) -> _SkyViewMilkyWayPreparedTileCacheEntry | None:

        self.last_tile_decode_success_count += result.decode_success_count
        self.last_tile_decode_failure_count += result.decode_failure_count
        if result.decode_failure_count > 0:
            self.last_tile_decode_failure_path = result.decode_failure_path
            self.last_tile_decode_failure_reason = result.decode_failure_reason
        elif result.decode_success_count > 0:
            self.last_tile_decode_failure_path = "none"
            self.last_tile_decode_failure_reason = "none"
        if count_as_render_path_work:
            self.last_tile_file_read_seconds += result.file_read_seconds
            self.last_tile_decode_seconds += result.decode_seconds
            self.last_tile_convert_seconds += result.convert_seconds
            self.last_tile_padding_seconds += result.padding_seconds
            self.last_tile_border_copy_seconds += result.padding_seconds
            self.last_tile_prepare_total_seconds += result.prepare_total_seconds
        else:
            self.last_prepared_tile_future_file_read_seconds += result.file_read_seconds
            self.last_prepared_tile_future_decode_seconds += result.decode_seconds
            self.last_prepared_tile_future_convert_seconds += result.convert_seconds
            self.last_prepared_tile_future_padding_seconds += result.padding_seconds
            self.last_prepared_tile_future_prepare_total_seconds += result.prepare_total_seconds

        entry = result.entry
        if entry is None:
            return None

        cache_key = tuple(result.cache_key)
        replaced_entry = self._prepared_tiles.get(cache_key)
        if replaced_entry is not None:
            self._prepared_tile_cache_total_bytes = max(0, self._prepared_tile_cache_total_bytes - replaced_entry.approx_bytes)
        self._prepared_tiles[cache_key] = entry
        self._prepared_tiles.move_to_end(cache_key)
        self._prepared_tile_cache_total_bytes += entry.approx_bytes
        self._evict_prepared_tiles_to_budget(cache_budget_bytes)
        return entry

    def _resolve_prepared_tile(
        self,
        tile_request: SkyViewMilkyWayTileDrawRequest,
        *,
        cache_budget_bytes: int,
    ) -> tuple[_SkyViewMilkyWayPreparedTileCacheEntry | None, bool]:

        benchmark_recorder = get_benchmark_recorder() if BENCHMARK_ENABLED else None

        benchmark_token = (

            benchmark_recorder.start_section("milky_way.prepared_tile_cache", metadata={"tile_id": self._tile_debug_id(tile_request)})

            if benchmark_recorder is not None

            else None

        )

        cache_key = tuple(tile_request.texture_cache_key)

        cached_entry = self._prepared_tiles.get(cache_key)

        if cached_entry is not None:

            self._prepared_tiles.move_to_end(cache_key)

            self.last_tile_cpu_cache_hits += 1

            if benchmark_recorder is not None:

                benchmark_recorder.stop_section(

                    benchmark_token,

                    metadata={"cache_status": "hit", "width": cached_entry.width, "height": cached_entry.height},

                )

            return cached_entry, True

        self.last_tile_cpu_cache_misses += 1

        prepare_start = perf_counter()

        prior_file_read_seconds = self.last_tile_file_read_seconds

        prior_decode_seconds = self.last_tile_decode_seconds

        prior_convert_seconds = self.last_tile_convert_seconds

        prior_padding_seconds = self.last_tile_padding_seconds

        padded_image, source_format, neighbor_tile_ids = self._load_tile_image_with_neighbor_border_details(
            tile_request,
            decoded_cache_budget_bytes=cache_budget_bytes,
        )

        if padded_image.isNull():

            if benchmark_recorder is not None:

                benchmark_recorder.stop_section(benchmark_token, metadata={"cache_status": "miss", "status": "decode-error"})

            return None, False

        intermediate_image = QImage(padded_image).mirrored(False, True)

        upload_image, _upload_status = self._prepare_milky_way_upload_image(intermediate_image)

        if not upload_image.isNull():

            upload_image = upload_image.mirrored(False, True)

        core_width, core_height = self._core_image_size(tile_request, padded_image, self._TILE_TEXTURE_BORDER_PX)

        prepare_total_seconds = perf_counter() - prepare_start

        self.last_tile_prepare_total_seconds += prepare_total_seconds

        entry = _SkyViewMilkyWayPreparedTileCacheEntry(
            upload_image=upload_image,
            width=int(upload_image.width()),
            height=int(upload_image.height()),
            core_width=core_width,
            core_height=core_height,
            border_px=self._TILE_TEXTURE_BORDER_PX,
            source_format=source_format,
            upload_format=self._image_format_name(upload_image),
            approx_bytes=self._image_approx_bytes(upload_image),
            neighbor_tile_ids=neighbor_tile_ids,
            prepare_total_seconds=prepare_total_seconds,
            file_read_seconds=self.last_tile_file_read_seconds - prior_file_read_seconds,
            decode_seconds=self.last_tile_decode_seconds - prior_decode_seconds,
            convert_seconds=self.last_tile_convert_seconds - prior_convert_seconds,
            padding_seconds=self.last_tile_padding_seconds - prior_padding_seconds,
        )

        self._prepared_tiles[cache_key] = entry

        self._prepared_tiles.move_to_end(cache_key)

        self._prepared_tile_cache_total_bytes += entry.approx_bytes

        self._evict_prepared_tiles_to_budget(cache_budget_bytes)

        if benchmark_recorder is not None:

            benchmark_recorder.stop_section(

                benchmark_token,

                metadata={

                    "cache_status": "miss",

                    "status": "ok",

                    "width": entry.width,

                    "height": entry.height,

                    "source_format": source_format,

                    "upload_format": entry.upload_format,

                    "neighbor_tiles": len(neighbor_tile_ids),

                    "file_read_seconds": entry.file_read_seconds,

                    "decode_seconds": entry.decode_seconds,

                    "convert_seconds": entry.convert_seconds,

                    "padding_seconds": entry.padding_seconds,

                },

            )

        return entry, False

    def _schedule_prepared_tile_future(
        self,
        tile_request: SkyViewMilkyWayTileDrawRequest,
    ) -> bool:

        if tile_request.has_signal is False or tile_request.is_missing:

            return False

        cache_key = tuple(tile_request.texture_cache_key)

        if cache_key in self._prepared_tiles or cache_key in self._prepared_tile_futures or cache_key in self._prepared_tile_future_errors:

            return False

        if len(self._prepared_tile_futures) >= self._PREPARED_TILE_FUTURE_MAX_PENDING:

            return False

        future = _MILKY_WAY_PREPARED_TILE_EXECUTOR.submit(
            OpenGLMilkyWayLayer._prepare_tile_for_upload_worker,
            tile_request,
        )
        self._prepared_tile_futures[cache_key] = future
        self._prepared_tile_future_errors.pop(cache_key, None)
        self.last_prepared_tile_future_submits += 1
        self.last_prepared_tile_future_pending = len(self._prepared_tile_futures)
        return True

    def _harvest_prepared_tile_futures(
        self,
        *,
        cache_budget_bytes: int,
        max_completed: int | None = None,
    ) -> int:

        resolved_limit = self._PREPARED_TILE_FUTURE_HARVEST_LIMIT_PER_FRAME if max_completed is None else max(0, int(max_completed))
        harvested_count = 0

        for cache_key, future in tuple(self._prepared_tile_futures.items()):

            if harvested_count >= resolved_limit:

                break

            if not future.done():

                continue

            self._prepared_tile_futures.pop(cache_key, None)
            try:

                result = future.result()

            except Exception as exc:

                self._prepared_tile_future_errors[cache_key] = str(exc) or exc.__class__.__name__
                self.last_prepared_tile_future_errors += 1
                harvested_count += 1
                continue

            self._store_prepared_tile_future_result(
                result,
                cache_budget_bytes=cache_budget_bytes,
                count_as_render_path_work=False,
            )
            if result.entry is None:

                self._prepared_tile_future_errors[cache_key] = result.decode_failure_reason

            harvested_count += 1

        self.last_prepared_tile_future_harvested += harvested_count
        self.last_prepared_tile_future_pending = len(self._prepared_tile_futures)
        self.last_prepared_tile_future_errors = len(self._prepared_tile_future_errors)
        return harvested_count

    def _cancel_prepared_tile_futures(self) -> None:

        for future in self._prepared_tile_futures.values():

            if not future.done():

                future.cancel()

        self._prepared_tile_futures.clear()
        self.last_prepared_tile_future_pending = 0

    def warm_prepared_tile_cache(
        self,
        tile_requests: tuple[SkyViewMilkyWayTileDrawRequest, ...],
        *,
        cache_budget_bytes: int,
        max_new_tiles: int | None = None,
    ) -> int:

        benchmark_recorder = get_benchmark_recorder() if BENCHMARK_ENABLED else None

        benchmark_token = (

            benchmark_recorder.start_section("milky_way.warm_prepared_tile_cache", metadata={"requested_tiles": len(tuple(tile_requests or ()))})

            if benchmark_recorder is not None

            else None

        )

        harvested_count = self._harvest_prepared_tile_futures(
            cache_budget_bytes=cache_budget_bytes,
            max_completed=self._PREPARED_TILE_FUTURE_HARVEST_LIMIT_PER_FRAME,
        )

        submitted_count = 0

        submit_limit = self._PREPARED_TILE_FUTURE_SUBMIT_LIMIT_PER_FRAME if max_new_tiles is None else max(0, int(max_new_tiles))

        for tile_request in tuple(tile_requests or ()):

            if submitted_count >= submit_limit:

                break

            if tile_request.has_signal is False or tile_request.is_missing:

                continue

            cache_key = tuple(tile_request.texture_cache_key)

            if cache_key in self._prepared_tiles:

                self._prepared_tiles.move_to_end(cache_key)

                continue

            if self._schedule_prepared_tile_future(tile_request):

                submitted_count += 1

        if benchmark_recorder is not None:

            benchmark_recorder.stop_section(
                benchmark_token,
                metadata={
                    "submitted_tiles": submitted_count,
                    "harvested_tiles": harvested_count,
                    "pending_tiles": len(self._prepared_tile_futures),
                },
            )

        return submitted_count

    def _resolve_tile_texture(
        self,
        tile_request: SkyViewMilkyWayTileDrawRequest,
        *,
        remaining_uploads: int,
        cache_budget_bytes: int,
        prepared_cache_budget_bytes: int,
        protected_cache_keys: set[tuple[object, ...]] | None = None,
        allow_blocking_tile_prepare: bool = False,
    ) -> tuple[_SkyViewMilkyWayTextureCacheEntry | None, bool, bool]:

        benchmark_recorder = get_benchmark_recorder() if BENCHMARK_ENABLED else None

        benchmark_token = (

            benchmark_recorder.start_section(

                "milky_way.tile_texture_cache",

                metadata={"tile_id": self._tile_debug_id(tile_request), "remaining_uploads": remaining_uploads},

            )

            if benchmark_recorder is not None

            else None

        )

        lookup_start = perf_counter()

        cache_key = tuple(tile_request.texture_cache_key)

        cached_entry = self._tile_textures.get(cache_key)

        if cached_entry is not None and cached_entry.texture.isCreated():

            self._tile_textures.move_to_end(cache_key)

            self.last_tile_cache_lookup_seconds += perf_counter() - lookup_start

            if benchmark_recorder is not None:

                benchmark_recorder.stop_section(

                    benchmark_token,

                    metadata={"cache_status": "hit", "uploaded": False, "width": cached_entry.width, "height": cached_entry.height},

                )

            return cached_entry, True, False

        if cached_entry is not None:

            self._release_texture(cached_entry.texture)

            self._tile_cache_total_bytes = max(0, self._tile_cache_total_bytes - cached_entry.approx_bytes)

            self._tile_textures.pop(cache_key, None)

        self.last_tile_cache_lookup_seconds += perf_counter() - lookup_start

        if tile_request.is_missing:

            if benchmark_recorder is not None:

                benchmark_recorder.stop_section(

                    benchmark_token,

                    metadata={"cache_status": "miss", "uploaded": False, "reason": "missing_file"},

                )

            return None, False, False

        prepared_entry = self._prepared_tiles.get(cache_key)

        if prepared_entry is not None:

            self._prepared_tiles.move_to_end(cache_key)

            self.last_tile_cpu_cache_hits += 1

        else:

            self.last_tile_cpu_cache_misses += 1

            if allow_blocking_tile_prepare and remaining_uploads > 0:

                prepared_entry, _prepared_cache_hit = self._resolve_prepared_tile(
                    tile_request,
                    cache_budget_bytes=prepared_cache_budget_bytes,
                )

            else:

                scheduled = self._schedule_prepared_tile_future(tile_request)

                if benchmark_recorder is not None:

                    benchmark_recorder.stop_section(

                        benchmark_token,

                        metadata={
                            "cache_status": "miss",
                            "uploaded": False,
                            "reason": "prepare_scheduled" if scheduled else "prepare_pending_or_throttled",
                            "prepared_future_pending": len(self._prepared_tile_futures),
                        },

                    )

                return None, False, False

        if prepared_entry is None:

            if benchmark_recorder is not None:

                benchmark_recorder.stop_section(

                    benchmark_token,

                    metadata={"cache_status": "miss", "uploaded": False, "reason": "prepare_failed"},

                )

            return None, False, False

        if remaining_uploads <= 0:

            if benchmark_recorder is not None:

                benchmark_recorder.stop_section(

                    benchmark_token,

                    metadata={"cache_status": "miss", "uploaded": False, "reason": "upload_limit", "prepared_cache_status": "hit"},

                )

            return None, False, False

        upload_start = perf_counter()

        try:

            texture, width, height, has_mipmaps = self._create_texture(
                prepared_entry.upload_image,
                repeat_s=False,
                upload_ready=True,
            )

        except Exception:

            if benchmark_recorder is not None:

                benchmark_recorder.stop_section(

                    benchmark_token,

                    metadata={"cache_status": "miss", "uploaded": False, "reason": "upload_failed"},

                )

            return None, False, False

        self.last_tile_upload_seconds += perf_counter() - upload_start

        entry = _SkyViewMilkyWayTextureCacheEntry(
            texture=texture,
            width=width,
            height=height,
            core_width=prepared_entry.core_width,
            core_height=prepared_entry.core_height,
            border_px=prepared_entry.border_px,
            has_mipmaps=has_mipmaps,
            approx_bytes=max(1, width * height * 4),
        )

        self._tile_textures[cache_key] = entry

        self._tile_textures.move_to_end(cache_key)

        self._tile_cache_total_bytes += entry.approx_bytes

        self._evict_tile_textures_to_budget(cache_budget_bytes, protected_cache_keys=protected_cache_keys)

        if benchmark_recorder is not None:

            benchmark_recorder.stop_section(

                benchmark_token,

                metadata={

                    "cache_status": "miss",

                    "uploaded": True,

                    "width": width,

                    "height": height,

                    "has_mipmaps": has_mipmaps,

                    "approx_bytes": entry.approx_bytes,

                },

            )

        return entry, False, True

    def _evict_tile_textures_to_budget(
        self,
        cache_budget_bytes: int,
        *,
        protected_cache_keys: set[tuple[object, ...]] | None = None,
    ) -> None:

        resolved_budget = max(1, int(cache_budget_bytes))

        protected = protected_cache_keys or set()

        evicted_count = 0

        evicted_bytes = 0

        while self._tile_textures and self._tile_cache_total_bytes > resolved_budget:

            cache_key = None

            for candidate_key in self._tile_textures.keys():

                if candidate_key not in protected:

                    cache_key = candidate_key

                    break

            if cache_key is None:

                break

            entry = self._tile_textures.pop(cache_key)

            self._release_texture(entry.texture)

            self._tile_cache_total_bytes = max(0, self._tile_cache_total_bytes - entry.approx_bytes)

            evicted_count += 1

            evicted_bytes += int(entry.approx_bytes)

        self.last_texture_eviction_count += evicted_count

        if evicted_count and BENCHMARK_ENABLED:

            get_benchmark_recorder().mark_event(

                "milky_way.texture_eviction",

                metadata={"count": evicted_count, "bytes": evicted_bytes, "budget_bytes": resolved_budget},

            )

    def _evict_prepared_tiles_to_budget(self, cache_budget_bytes: int) -> None:

        resolved_budget = max(1, int(cache_budget_bytes))

        evicted_count = 0

        evicted_bytes = 0

        while self._prepared_tiles and self._prepared_tile_cache_total_bytes > resolved_budget:

            _cache_key, entry = self._prepared_tiles.popitem(last=False)

            self._prepared_tile_cache_total_bytes = max(0, self._prepared_tile_cache_total_bytes - entry.approx_bytes)

            evicted_count += 1

            evicted_bytes += int(entry.approx_bytes)

        self.last_prepared_tile_eviction_count += evicted_count

        if evicted_count and BENCHMARK_ENABLED:

            get_benchmark_recorder().mark_event(

                "milky_way.prepared_tile_eviction",

                metadata={"count": evicted_count, "bytes": evicted_bytes, "budget_bytes": resolved_budget},

            )

    def _evict_decoded_tiles_to_budget(self, cache_budget_bytes: int) -> None:

        resolved_budget = max(1, int(cache_budget_bytes))

        evicted_count = 0

        evicted_bytes = 0

        while self._decoded_tiles and self._decoded_tile_cache_total_bytes > resolved_budget:

            _cache_key, entry = self._decoded_tiles.popitem(last=False)

            self._decoded_tile_cache_total_bytes = max(0, self._decoded_tile_cache_total_bytes - entry.approx_bytes)

            evicted_count += 1

            evicted_bytes += int(entry.approx_bytes)

        self.last_decoded_tile_eviction_count += evicted_count

        if evicted_count and BENCHMARK_ENABLED:

            get_benchmark_recorder().mark_event(

                "milky_way.decoded_tile_eviction",

                metadata={"count": evicted_count, "bytes": evicted_bytes, "budget_bytes": resolved_budget},

            )

    def _destroy_tile_textures(self) -> None:

        while self._tile_textures:

            _cache_key, entry = self._tile_textures.popitem(last=False)

            self._release_texture(entry.texture)

        self._tile_cache_total_bytes = 0

    def _destroy_prepared_tiles(self) -> None:

        self._cancel_prepared_tile_futures()

        self._prepared_tiles.clear()

        self._prepared_tile_cache_total_bytes = 0

    def _destroy_decoded_tiles(self) -> None:

        self._decoded_tiles.clear()

        self._decoded_tile_cache_total_bytes = 0

    @staticmethod
    def _release_texture(texture: QOpenGLTexture) -> None:

        try:

            texture.release()

        except Exception:

            pass

        try:

            texture.destroy()

        except Exception:

            pass

    def _set_uniform_values(self, functions: QOpenGLFunctions_2_0, uniforms: SkyViewMilkyWayGlUniforms) -> None:

        functions.glUniform4f(
            self._uniform_locations["u_viewport_rect"],
            float(uniforms.viewport_rect.left()),
            float(uniforms.viewport_rect.top()),
            float(uniforms.viewport_rect.width()),
            float(uniforms.viewport_rect.height()),
        )

        functions.glUniform2f(
            self._uniform_locations["u_viewport_size_px"],
            float(uniforms.viewport_width_px),
            float(uniforms.viewport_height_px),
        )

        functions.glUniform1f(self._uniform_locations["u_device_pixel_ratio"], float(uniforms.device_pixel_ratio))

        functions.glUniform3f(self._uniform_locations["u_equatorial_forward"], *uniforms.equatorial_forward)

        functions.glUniform3f(self._uniform_locations["u_equatorial_up"], *uniforms.equatorial_up)

        functions.glUniform3f(self._uniform_locations["u_equatorial_right"], *uniforms.equatorial_right)

        functions.glUniform1f(self._uniform_locations["u_half_horizontal_fov_rad"], float(uniforms.half_horizontal_fov_rad))

        functions.glUniform1f(self._uniform_locations["u_half_vertical_fov_rad"], float(uniforms.half_vertical_fov_rad))

        functions.glUniform1f(self._uniform_locations["u_field_width_deg"], float(uniforms.field_width_deg))

        functions.glUniform1f(self._uniform_locations["u_opacity"], max(0.0, min(1.0, float(uniforms.opacity))))

        functions.glUniform1f(self._uniform_locations["u_brightness"], max(0.0, float(uniforms.brightness)))

        functions.glUniform1f(self._uniform_locations["u_contrast"], max(0.0, float(uniforms.contrast)))

        functions.glUniform1f(self._uniform_locations["u_saturation"], max(0.0, float(uniforms.saturation)))

        functions.glUniform1f(self._uniform_locations["u_black_point"], max(0.0, min(0.95, float(uniforms.black_point))))

        functions.glUniform1f(self._uniform_locations["u_gamma"], max(0.05, float(uniforms.gamma)))

        functions.glUniform1f(self._uniform_locations["u_flip_horizontal"], 1.0 if uniforms.flip_horizontal else 0.0)

        functions.glUniform1f(self._uniform_locations["u_texture_mode"], 0.0)

        functions.glUniform1f(
            self._uniform_locations["u_alpha_mode"],
            1.0 if str(uniforms.alpha_mode or "").strip().casefold() == "luminance_debug" else 0.0,
        )

        functions.glUniform1f(
            self._uniform_locations["u_alpha_mask_black"],
            max(0.0, min(1.0, float(uniforms.alpha_mask_black))),
        )

        functions.glUniform1f(
            self._uniform_locations["u_alpha_mask_white"],
            max(float(uniforms.alpha_mask_black) + 0.001, min(1.0, float(uniforms.alpha_mask_white))),
        )

        functions.glUniform1f(self._uniform_locations["u_debug_output_mode"], 0.0)

        functions.glUniform1f(self._uniform_locations["u_debug_sample_mode"], 0.0)

        functions.glUniform1f(self._uniform_locations["u_debug_uv_mode"], 0.0)

        functions.glUniform4f(self._uniform_locations["u_tile_uv_bounds"], 0.0, 1.0, 0.0, 1.0)

        functions.glUniform2f(self._uniform_locations["u_tile_dec_bounds_deg"], -90.0, 90.0)

        functions.glUniform1f(self._uniform_locations["u_tile_include_max_v"], 0.0)

        functions.glUniform3f(self._uniform_locations["u_tile_debug_color"], 0.0, 0.0, 0.0)

        functions.glUniform3f(self._uniform_locations["u_tile_debug_exact_id"], 0.0, 0.0, 0.0)

        functions.glUniform2f(self._uniform_locations["u_tile_texture_size"], 1.0, 1.0)

        functions.glUniform2f(self._uniform_locations["u_tile_texture_core_size"], 1.0, 1.0)

        functions.glUniform2f(self._uniform_locations["u_tile_texture_border_px"], 0.0, 0.0)

        functions.glUniform1i(self._uniform_locations["u_texture"], 0)

    def _set_texture_mode(self, functions: QOpenGLFunctions_2_0, *, local_uv: bool = False, shader_tile: bool = False) -> None:

        texture_mode = 2.0 if shader_tile else 1.0 if local_uv else 0.0

        functions.glUniform1f(self._uniform_locations["u_texture_mode"], texture_mode)

    def _set_tile_uniforms(
        self,
        functions: QOpenGLFunctions_2_0,
        tile_request: SkyViewMilkyWayTileDrawRequest,
        *,
        tile_texture_entry: _SkyViewMilkyWayTextureCacheEntry | None = None,
    ) -> None:

        if tile_request.tile_renderer == "projected_mesh_debug":

            self._set_texture_mode(functions, local_uv=True)

        else:

            self._set_texture_mode(functions, shader_tile=True)

        functions.glUniform4f(self._uniform_locations["u_tile_uv_bounds"], *tile_request.uv_bounds)

        functions.glUniform2f(self._uniform_locations["u_tile_dec_bounds_deg"], *tile_request.dec_bounds_deg)

        functions.glUniform1f(self._uniform_locations["u_tile_include_max_v"], 1.0 if tile_request.include_max_v else 0.0)

        functions.glUniform3f(
            self._uniform_locations["u_tile_debug_color"],
            *self._tile_debug_color_components(tile_request),
        )

        functions.glUniform3f(
            self._uniform_locations["u_tile_debug_exact_id"],
            float(max(0, min(255, int(tile_request.level)))) / 255.0,
            float(max(0, min(255, int(tile_request.x_index)))) / 255.0,
            float(max(0, min(255, int(tile_request.y_index)))) / 255.0,
        )

        texture_width = float(tile_texture_entry.width if tile_texture_entry is not None else 1)

        texture_height = float(tile_texture_entry.height if tile_texture_entry is not None else 1)

        functions.glUniform2f(
            self._uniform_locations["u_tile_texture_size"],
            max(1.0, texture_width),
            max(1.0, texture_height),
        )

        core_width = float(tile_texture_entry.core_width if tile_texture_entry is not None else texture_width)

        core_height = float(tile_texture_entry.core_height if tile_texture_entry is not None else texture_height)

        border_px = float(tile_texture_entry.border_px if tile_texture_entry is not None else 0)

        functions.glUniform2f(
            self._uniform_locations["u_tile_texture_core_size"],
            max(1.0, core_width),
            max(1.0, core_height),
        )

        functions.glUniform2f(
            self._uniform_locations["u_tile_texture_border_px"],
            max(0.0, border_px),
            max(0.0, border_px),
        )

    def _set_debug_override_uniforms(self, functions: QOpenGLFunctions_2_0, diagnostic_override: dict[str, object] | None) -> None:

        def _float_pair(value: object, *, default: tuple[float, float]) -> tuple[float, float]:

            if isinstance(value, (list, tuple)) and len(value) >= 2:

                return float(value[0]), float(value[1])

            return default

        def _float_triplet(value: object, *, default: tuple[float, float, float]) -> tuple[float, float, float]:

            if isinstance(value, (list, tuple)) and len(value) >= 3:

                return float(value[0]), float(value[1]), float(value[2])

            return default

        def _int_triplet(value: object, *, default: tuple[int, int, int]) -> tuple[int, int, int]:

            if isinstance(value, (list, tuple)) and len(value) >= 3:

                return int(value[0]), int(value[1]), int(value[2])

            return default

        if not isinstance(diagnostic_override, dict):

            functions.glUniform1f(self._uniform_locations["u_debug_override_enabled"], 0.0)

            functions.glUniform2f(self._uniform_locations["u_debug_override_local_uv"], 0.0, 0.0)

            functions.glUniform2f(self._uniform_locations["u_debug_override_global_uv"], 0.0, 0.0)

            functions.glUniform3f(self._uniform_locations["u_debug_override_raw_rgb"], 0.0, 0.0, 0.0)

            functions.glUniform3f(self._uniform_locations["u_debug_override_toned_rgb"], 0.0, 0.0, 0.0)

            functions.glUniform1f(self._uniform_locations["u_debug_override_alpha"], 0.0)

            functions.glUniform3f(self._uniform_locations["u_debug_override_tile_id"], 0.0, 0.0, 0.0)

            return

        local_uv = _float_pair(diagnostic_override.get("local_uv"), default=(0.0, 0.0))

        global_uv = _float_pair(diagnostic_override.get("global_uv"), default=(0.0, 0.0))

        raw_rgb = _float_triplet(diagnostic_override.get("raw_rgb"), default=(0.0, 0.0, 0.0))

        toned_rgb = _float_triplet(diagnostic_override.get("toned_rgb"), default=(0.0, 0.0, 0.0))

        alpha_value = diagnostic_override.get("alpha", 0.0)

        alpha = max(0.0, min(1.0, float(alpha_value if isinstance(alpha_value, (int, float)) else 0.0)))

        tile_id = _int_triplet(diagnostic_override.get("tile_id"), default=(0, 0, 0))

        functions.glUniform1f(self._uniform_locations["u_debug_override_enabled"], 1.0)

        functions.glUniform2f(
            self._uniform_locations["u_debug_override_local_uv"],
            max(0.0, min(1.0, float(local_uv[0]))),
            max(0.0, min(1.0, float(local_uv[1]))),
        )

        functions.glUniform2f(
            self._uniform_locations["u_debug_override_global_uv"],
            max(0.0, min(1.0, float(global_uv[0]))),
            max(0.0, min(1.0, float(global_uv[1]))),
        )

        functions.glUniform3f(
            self._uniform_locations["u_debug_override_raw_rgb"],
            max(0.0, min(1.0, float(raw_rgb[0]))),
            max(0.0, min(1.0, float(raw_rgb[1]))),
            max(0.0, min(1.0, float(raw_rgb[2]))),
        )

        functions.glUniform3f(
            self._uniform_locations["u_debug_override_toned_rgb"],
            max(0.0, min(1.0, float(toned_rgb[0]))),
            max(0.0, min(1.0, float(toned_rgb[1]))),
            max(0.0, min(1.0, float(toned_rgb[2]))),
        )

        functions.glUniform1f(self._uniform_locations["u_debug_override_alpha"], alpha)

        functions.glUniform3f(
            self._uniform_locations["u_debug_override_tile_id"],
            float(max(0, min(255, int(tile_id[0])))) / 255.0,
            float(max(0, min(255, int(tile_id[1])))) / 255.0,
            float(max(0, min(255, int(tile_id[2])))) / 255.0,
        )

    def _draw_fullscreen_triangle(self, functions: QOpenGLFunctions_2_0) -> None:

        functions.glBegin(self._GL_TRIANGLES)

        functions.glTexCoord2f(0.0, 0.0)

        functions.glVertex2f(-1.0, -1.0)

        functions.glTexCoord2f(2.0, 0.0)

        functions.glVertex2f(3.0, -1.0)

        functions.glTexCoord2f(0.0, 2.0)

        functions.glVertex2f(-1.0, 3.0)

        functions.glEnd()

    def _draw_tile_mesh(self, functions: QOpenGLFunctions_2_0, tile_request: SkyViewMilkyWayTileDrawRequest) -> None:

        if not tile_request.vertices:

            return

        functions.glBegin(self._GL_TRIANGLES)

        for vertex in tile_request.vertices:

            functions.glTexCoord2f(float(vertex.texture_u), float(vertex.texture_v))

            functions.glVertex2f(float(vertex.clip_x), float(vertex.clip_y))

        functions.glEnd()

    def _draw_tile_quad(self, functions: QOpenGLFunctions_2_0, clip_bounds: tuple[float, float, float, float]) -> None:

        clip_left, clip_right, clip_top, clip_bottom = clip_bounds

        functions.glBegin(self._GL_TRIANGLES)

        functions.glTexCoord2f(0.0, 0.0)
        functions.glVertex2f(float(clip_left), float(clip_top))
        functions.glTexCoord2f(1.0, 0.0)
        functions.glVertex2f(float(clip_right), float(clip_top))
        functions.glTexCoord2f(1.0, 1.0)
        functions.glVertex2f(float(clip_right), float(clip_bottom))

        functions.glTexCoord2f(0.0, 0.0)
        functions.glVertex2f(float(clip_left), float(clip_top))
        functions.glTexCoord2f(1.0, 1.0)
        functions.glVertex2f(float(clip_right), float(clip_bottom))
        functions.glTexCoord2f(0.0, 1.0)
        functions.glVertex2f(float(clip_left), float(clip_bottom))

        functions.glEnd()

    def _draw_tile_request(self, functions: QOpenGLFunctions_2_0, tile_request: SkyViewMilkyWayTileDrawRequest) -> None:

        if tile_request.tile_renderer == "projected_mesh_debug":

            self._draw_tile_mesh(functions, tile_request)

            return

        self._draw_tile_quad(functions, tile_request.clip_bounds)

    @staticmethod
    def shader_tile_contains_sample(tile_request: SkyViewMilkyWayTileDrawRequest, *, global_u: float, dec_deg: float) -> bool:

        u_min, u_max, _, _ = tile_request.uv_bounds

        dec_min_deg, dec_max_deg = tile_request.dec_bounds_deg

        inside_u = float(global_u) >= float(u_min) and float(global_u) < float(u_max)

        inside_v = float(dec_deg) >= float(dec_min_deg) and (
            float(dec_deg) < float(dec_max_deg)
            or (tile_request.include_max_v and float(dec_deg) <= float(dec_max_deg))
        )

        return inside_u and inside_v

    @classmethod
    def shader_tile_local_uv(cls, tile_request: SkyViewMilkyWayTileDrawRequest, *, global_u: float, dec_deg: float) -> tuple[float, float] | None:

        if not cls.shader_tile_contains_sample(tile_request, global_u=global_u, dec_deg=dec_deg):

            return None

        u_min, u_max, _, _ = tile_request.uv_bounds

        dec_min_deg, dec_max_deg = tile_request.dec_bounds_deg

        local_u = (float(global_u) - float(u_min)) / max(float(u_max) - float(u_min), 1.0e-6)

        local_v = (float(dec_max_deg) - float(dec_deg)) / max(float(dec_max_deg) - float(dec_min_deg), 1.0e-6)

        return local_u, local_v

    @staticmethod
    def _tile_debug_id(tile_request: SkyViewMilkyWayTileDrawRequest) -> str:

        return f"L{int(tile_request.level)}/{int(tile_request.x_index)}/{int(tile_request.y_index)}"

    @staticmethod
    def _debug_output_mode_value(mode: str, *, debug_uv_enabled: bool) -> int:

        if bool(debug_uv_enabled):

            return 2

        return {
            "final": 0,
            "tile_id_color": 1,
            "tile_solid_id_color": 1,
            "local_uv": 2,
            "global_uv": 3,
            "raw_tile_rgb": 4,
            "tile_raw_opaque": 4,
            "tile_raw_normal_alpha": 5,
            "toned_rgb": 6,
            "alpha": 7,
            "tile_alpha_mask": 7,
            "owner_mask": 8,
            "tile_coverage_mask": 8,
            "tile_id_exact": 9,
            "local_uv_packed": 10,
            "global_uv_packed": 11,
            "raw_tile_rgb_packed_hi": 12,
            "raw_tile_rgb_packed_lo": 13,
            "toned_rgb_packed_hi": 14,
            "toned_rgb_packed_lo": 15,
            "alpha_packed": 16,
            "final_preblend_packed_hi": 17,
            "final_preblend_packed_lo": 18,
        }.get(str(mode or "final").strip().casefold(), 0)

    @staticmethod
    def _debug_output_requires_exact_write(mode: str, *, debug_uv_enabled: bool) -> bool:

        if bool(debug_uv_enabled):

            return False

        return str(mode or "final").strip().casefold() in {
            "tile_id_color",
            "tile_solid_id_color",
            "raw_tile_rgb",
            "tile_raw_opaque",
            "owner_mask",
            "tile_coverage_mask",
            "tile_id_exact",
            "local_uv_packed",
            "global_uv_packed",
            "raw_tile_rgb_packed_hi",
            "raw_tile_rgb_packed_lo",
            "toned_rgb_packed_hi",
            "toned_rgb_packed_lo",
            "alpha_packed",
            "final_preblend_packed_hi",
            "final_preblend_packed_lo",
        }

    @staticmethod
    def _debug_sample_mode_value(mode: str) -> int:

        return {
            "normal": 0,
            "nearest": 1,
            "linear_no_mip": 2,
            "linear_mip": 3,
            "texel_fetch_debug": 4,
            "manual_bilinear_debug": 5,
        }.get(str(mode or "normal").strip().casefold(), 0)

    @staticmethod
    def _tile_debug_color_components(tile_request: SkyViewMilkyWayTileDrawRequest) -> tuple[float, float, float]:

        return (
            float(((int(tile_request.x_index) * 53) + (int(tile_request.level) * 17)) % 256) / 255.0,
            float(((int(tile_request.y_index) * 97) + (int(tile_request.level) * 29)) % 256) / 255.0,
            float(((int(tile_request.x_index) * 11) + (int(tile_request.y_index) * 19) + (int(tile_request.level) * 71)) % 256) / 255.0,
        )

    def _apply_tile_texture_sample_mode(
        self,
        texture: QOpenGLTexture,
        *,
        sample_mode: str,
        has_mipmaps: bool,
    ) -> None:

        resolved_mode = str(sample_mode or "normal").strip().casefold()

        if resolved_mode in {"nearest", "texel_fetch_debug", "manual_bilinear_debug"}:

            texture.setMagnificationFilter(QOpenGLTexture.Filter.Nearest)

            texture.setMinificationFilter(QOpenGLTexture.Filter.Nearest)

            return

        texture.setMagnificationFilter(QOpenGLTexture.Filter.Linear)

        if resolved_mode == "linear_no_mip":

            texture.setMinificationFilter(QOpenGLTexture.Filter.Linear)

            return

        if resolved_mode == "linear_mip" and has_mipmaps:

            texture.setMinificationFilter(QOpenGLTexture.Filter.LinearMipMapLinear)

            return

        if has_mipmaps:

            texture.setMinificationFilter(QOpenGLTexture.Filter.LinearMipMapLinear)

            return

        texture.setMinificationFilter(QOpenGLTexture.Filter.Linear)

    def _capture_tile_texture_state(
        self,
        functions: QOpenGLFunctions_2_0,
        *,
        tile_request: SkyViewMilkyWayTileDrawRequest,
        tile_texture_entry: _SkyViewMilkyWayTextureCacheEntry,
        sample_mode: str,
    ) -> dict[str, object]:

        unpack_alignment = self._integer_values(functions, self._GL_UNPACK_ALIGNMENT, 1)

        texture_id = 0

        try:

            texture_id = int(tile_texture_entry.texture.textureId())

        except Exception:

            texture_id = 0

        width = self._texture_level_parameter_value(functions, self._GL_TEXTURE_WIDTH)

        height = self._texture_level_parameter_value(functions, self._GL_TEXTURE_HEIGHT)

        internal_format = self._texture_level_parameter_value(functions, self._GL_TEXTURE_INTERNAL_FORMAT)

        return {
            "tile_id": self._tile_debug_id(tile_request),
            "texture_id": texture_id,
            "sample_mode": str(sample_mode or "normal"),
            "width": int(width if width is not None else tile_texture_entry.width),
            "height": int(height if height is not None else tile_texture_entry.height),
            "core_width": int(tile_texture_entry.core_width),
            "core_height": int(tile_texture_entry.core_height),
            "border_px": int(tile_texture_entry.border_px),
            "has_mipmaps": bool(tile_texture_entry.has_mipmaps),
            "min_filter": self._gl_enum_name(self._texture_parameter_value(functions, self._GL_TEXTURE_MIN_FILTER)),
            "mag_filter": self._gl_enum_name(self._texture_parameter_value(functions, self._GL_TEXTURE_MAG_FILTER)),
            "wrap_s": self._gl_enum_name(self._texture_parameter_value(functions, self._GL_TEXTURE_WRAP_S)),
            "wrap_t": self._gl_enum_name(self._texture_parameter_value(functions, self._GL_TEXTURE_WRAP_T)),
            "base_level": self._texture_parameter_value(functions, self._GL_TEXTURE_BASE_LEVEL),
            "max_level": self._texture_parameter_value(functions, self._GL_TEXTURE_MAX_LEVEL),
            "internal_format": self._gl_enum_name(internal_format),
            "unpack_alignment": int(unpack_alignment[0]) if unpack_alignment is not None else None,
        }

    @staticmethod
    def _texture_parameter_value(functions: QOpenGLFunctions_2_0, parameter_name: int) -> int | None:

        values = [0]

        try:

            getattr(functions, "glGetTexParameteriv")(OpenGLMilkyWayLayer._GL_TEXTURE_2D, parameter_name, values)

        except Exception:

            return None

        return int(values[0])

    @staticmethod
    def _texture_level_parameter_value(functions: QOpenGLFunctions_2_0, parameter_name: int) -> int | None:

        values = [0]

        try:

            getattr(functions, "glGetTexLevelParameteriv")(OpenGLMilkyWayLayer._GL_TEXTURE_2D, 0, parameter_name, values)

        except Exception:

            return None

        return int(values[0])

    @classmethod
    def _gl_enum_name(cls, value: int | None) -> str | None:

        if value is None:

            return None

        known_values = {
            cls._GL_NEAREST: "GL_NEAREST",
            cls._GL_LINEAR: "GL_LINEAR",
            cls._GL_LINEAR_MIPMAP_LINEAR: "GL_LINEAR_MIPMAP_LINEAR",
            0x812F: "GL_CLAMP_TO_EDGE",
            0x2901: "GL_REPEAT",
            0x1907: "GL_RGB",
            0x8051: "GL_RGB8",
            0x1908: "GL_RGBA",
            0x8058: "GL_RGBA8",
        }

        return known_values.get(int(value), hex(int(value)))

    @staticmethod
    def _integer_values(functions: QOpenGLFunctions_2_0, parameter_name: int, value_count: int) -> tuple[int, ...] | None:

        values = [0 for _ in range(value_count)]

        try:

            getattr(functions, "glGetIntegerv")(parameter_name, values)

        except Exception:

            return None

        return tuple(int(value) for value in values)

    @staticmethod
    def _is_enabled(functions: QOpenGLFunctions_2_0, capability: int) -> bool | None:

        try:

            return bool(functions.glIsEnabled(capability))

        except Exception:

            return None

    @staticmethod
    def _restore_enabled_states(functions: QOpenGLFunctions_2_0, states: dict[int, bool | None]) -> None:

        for capability, was_enabled in states.items():

            if was_enabled is None:

                functions.glDisable(capability)

            elif was_enabled:

                functions.glEnable(capability)

            else:

                functions.glDisable(capability)

    def _check_gl_error(self, functions: QOpenGLFunctions_2_0, context: str) -> None:

        try:

            error_code = int(functions.glGetError())

        except Exception:

            return

        if error_code != self._GL_NO_ERROR:

            raise RuntimeError(f"OpenGL error {error_code} {context}")