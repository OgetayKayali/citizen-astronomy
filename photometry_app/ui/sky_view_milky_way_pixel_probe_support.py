from __future__ import annotations

import json
import math
from collections import Counter, defaultdict
from pathlib import Path

import numpy as np


DEFAULT_WEDGE_BBOX = (0.44, 0.26, 0.68, 0.60)


def clamp01(value: float) -> float:
    return max(0.0, min(1.0, float(value)))


def viewport_device_size(viewport_width_logical: float, viewport_height_logical: float, device_pixel_ratio: float) -> tuple[int, int]:
    resolved_dpr = max(1.0e-6, float(device_pixel_ratio))
    return (
        max(1, int(math.ceil(float(viewport_width_logical) * resolved_dpr))),
        max(1, int(math.ceil(float(viewport_height_logical) * resolved_dpr))),
    )


def logical_to_framebuffer_xy(logical_x: float, logical_y: float, device_pixel_ratio: float) -> tuple[int, int]:
    resolved_dpr = max(1.0e-6, float(device_pixel_ratio))
    return (
        max(0, int(math.floor(float(logical_x) * resolved_dpr))),
        max(0, int(math.floor(float(logical_y) * resolved_dpr))),
    )


def screenshot_to_logical_xy(screenshot_x: int, screenshot_y: int, device_pixel_ratio: float) -> tuple[float, float]:
    resolved_dpr = max(1.0e-6, float(device_pixel_ratio))
    return (
        (float(screenshot_x) + 0.5) / resolved_dpr,
        (float(screenshot_y) + 0.5) / resolved_dpr,
    )


def top_left_to_bottom_left_readback_y(top_left_y: int, framebuffer_height: int, *, probe_height: int = 1) -> int:
    resolved_probe_height = max(1, int(probe_height))
    resolved_height = max(resolved_probe_height, int(framebuffer_height))
    return max(0, resolved_height - resolved_probe_height - int(top_left_y))


def build_capture_coordinate_record(
    screenshot_x: int,
    screenshot_y: int,
    *,
    viewport_width_logical: float,
    viewport_height_logical: float,
    device_pixel_ratio: float,
    probe_width: int = 1,
    probe_height: int | None = None,
) -> dict[str, object]:
    resolved_probe_width = max(1, int(probe_width))
    resolved_probe_height = resolved_probe_width if probe_height is None else max(1, int(probe_height))
    viewport_width_device, viewport_height_device = viewport_device_size(
        viewport_width_logical,
        viewport_height_logical,
        device_pixel_ratio,
    )
    clamped_screenshot_x = max(0, min(viewport_width_device - 1, int(screenshot_x)))
    clamped_screenshot_y = max(0, min(viewport_height_device - 1, int(screenshot_y)))
    logical_x, logical_y = screenshot_to_logical_xy(clamped_screenshot_x, clamped_screenshot_y, device_pixel_ratio)
    return {
        "logical_x": round(float(logical_x), 6),
        "logical_y": round(float(logical_y), 6),
        "device_pixel_ratio": round(float(device_pixel_ratio), 6),
        "framebuffer_x": clamped_screenshot_x,
        "framebuffer_y": clamped_screenshot_y,
        "opengl_readback_x": clamped_screenshot_x,
        "opengl_readback_y": top_left_to_bottom_left_readback_y(
            clamped_screenshot_y,
            viewport_height_device,
            probe_height=resolved_probe_height,
        ),
        "screenshot_x": clamped_screenshot_x,
        "screenshot_y": clamped_screenshot_y,
        "viewport_width_logical": round(float(viewport_width_logical), 6),
        "viewport_height_logical": round(float(viewport_height_logical), 6),
        "viewport_width_device": viewport_width_device,
        "viewport_height_device": viewport_height_device,
        "probe_width": resolved_probe_width,
        "probe_height": resolved_probe_height,
    }


def screen_region(width: int, height: int, x_pixel: int, y_pixel: int) -> str:
    column = min(2, max(0, int((3 * int(x_pixel)) / max(int(width), 1))))
    row = min(2, max(0, int((3 * int(y_pixel)) / max(int(height), 1))))
    return f"r{row}_c{column}"


def decode_u16(high_byte: int, low_byte: int) -> float:
    return ((int(high_byte) << 8) | int(low_byte)) / 65535.0


def decode_packed_uv(pixel_rgba: np.ndarray) -> np.ndarray:
    return np.asarray(
        (
            decode_u16(int(pixel_rgba[0]), int(pixel_rgba[1])),
            decode_u16(int(pixel_rgba[2]), int(pixel_rgba[3])),
        ),
        dtype=np.float64,
    )


def decode_packed_vec3(high_pixel: np.ndarray, low_pixel: np.ndarray) -> np.ndarray:
    return np.asarray(
        (
            decode_u16(int(high_pixel[0]), int(low_pixel[0])),
            decode_u16(int(high_pixel[1]), int(low_pixel[1])),
            decode_u16(int(high_pixel[2]), int(low_pixel[2])),
        ),
        dtype=np.float64,
    )


def decode_tile_id_exact(pixel_rgb: np.ndarray) -> tuple[int, int, int]:
    return int(pixel_rgb[0]), int(pixel_rgb[1]), int(pixel_rgb[2])


def sample_image_bilinear_from_coords(image_rgb: np.ndarray, x_coord: float, y_coord: float) -> np.ndarray:
    height, width, _channels = image_rgb.shape
    x_value = max(0.0, min(float(width - 1), float(x_coord)))
    y_value = max(0.0, min(float(height - 1), float(y_coord)))
    x0 = int(math.floor(x_value))
    y0 = int(math.floor(y_value))
    x1 = min(width - 1, x0 + 1)
    y1 = min(height - 1, y0 + 1)
    tx = x_value - float(x0)
    ty = y_value - float(y0)
    top = ((1.0 - tx) * image_rgb[y0, x0]) + (tx * image_rgb[y0, x1])
    bottom = ((1.0 - tx) * image_rgb[y1, x0]) + (tx * image_rgb[y1, x1])
    return ((1.0 - ty) * top) + (ty * bottom)


def sample_model_gl_linear(image_rgb: np.ndarray, u_value: float, v_value: float) -> np.ndarray:
    height, width, _channels = image_rgb.shape
    return sample_image_bilinear_from_coords(
        image_rgb,
        (clamp01(u_value) * float(width)) - 0.5,
        (clamp01(v_value) * float(height)) - 0.5,
    )


def sample_model_edge_inclusive(image_rgb: np.ndarray, u_value: float, v_value: float) -> np.ndarray:
    height, width, _channels = image_rgb.shape
    return sample_image_bilinear_from_coords(
        image_rgb,
        clamp01(u_value) * float(max(width - 1, 0)),
        clamp01(v_value) * float(max(height - 1, 0)),
    )


def sample_model_half_pixel(image_rgb: np.ndarray, u_value: float, v_value: float) -> np.ndarray:
    height, width, _channels = image_rgb.shape
    return sample_image_bilinear_from_coords(
        image_rgb,
        clamp01(u_value) * float(width),
        clamp01(v_value) * float(height),
    )


def sample_model_nearest_center(image_rgb: np.ndarray, u_value: float, v_value: float) -> np.ndarray:
    height, width, _channels = image_rgb.shape
    x_index = max(0, min(width - 1, int(math.floor(clamp01(u_value) * float(width)))))
    y_index = max(0, min(height - 1, int(math.floor(clamp01(v_value) * float(height)))))
    return image_rgb[y_index, x_index]


def mae(left: np.ndarray, right: np.ndarray) -> float:
    return float(np.mean(np.abs(left.astype(np.float64) - right.astype(np.float64))))


def evaluate_sampling_models(tile_image: np.ndarray, local_u: float, local_v: float, actual_raw_rgb: np.ndarray) -> tuple[dict[str, dict[str, object]], str, float]:
    transformed_uvs = {
        "gl_linear": (local_u, local_v, sample_model_gl_linear),
        "edge_inclusive": (local_u, local_v, sample_model_edge_inclusive),
        "half_pixel": (local_u, local_v, sample_model_half_pixel),
        "nearest": (local_u, local_v, sample_model_nearest_center),
        "gl_linear_flip_v": (local_u, 1.0 - local_v, sample_model_gl_linear),
        "gl_linear_flip_u": (1.0 - local_u, local_v, sample_model_gl_linear),
        "gl_linear_flip_uv": (1.0 - local_u, 1.0 - local_v, sample_model_gl_linear),
        "half_pixel_flip_v": (local_u, 1.0 - local_v, sample_model_half_pixel),
        "half_pixel_flip_u": (1.0 - local_u, local_v, sample_model_half_pixel),
        "half_pixel_flip_uv": (1.0 - local_u, 1.0 - local_v, sample_model_half_pixel),
    }
    sampling_models: dict[str, dict[str, object]] = {}
    for model_name, (model_u, model_v, sampler) in transformed_uvs.items():
        expected_rgb = sampler(tile_image, model_u, model_v)
        sampling_models[model_name] = {
            "uv": [round(float(model_u), 6), round(float(model_v), 6)],
            "expected_rgb": [round(float(value), 6) for value in expected_rgb.tolist()],
            "mae": round(mae(actual_raw_rgb, expected_rgb), 6),
        }
    best_model_name, best_model_payload = min(sampling_models.items(), key=lambda item: float(item[1]["mae"]))
    return sampling_models, best_model_name, float(best_model_payload["mae"])


def load_probe_samples_from_json(path: Path, *, width: int, height: int) -> list[dict[str, object]]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    sample_entries = payload.get("samples", payload) if isinstance(payload, dict) else payload
    resolved: list[dict[str, object]] = []
    for index, entry in enumerate(sample_entries):
        x_value = max(0, min(int(width - 1), int(entry["x"])))
        y_value = max(0, min(int(height - 1), int(entry["y"])))
        classification = str(entry.get("classification", "manual"))
        tags = tuple(str(tag) for tag in entry.get("tags", (classification,)))
        resolved.append(
            {
                "sample_id": str(entry.get("sample_id", f"manual_{index:02d}")),
                "x": x_value,
                "y": y_value,
                "classification": classification,
                "tags": list(tags),
                "screen_region": screen_region(width, height, x_value, y_value),
            }
        )
    return resolved


def build_default_probe_samples(
    final_rgba8: np.ndarray,
    *,
    wedge_bbox: tuple[float, float, float, float] = DEFAULT_WEDGE_BBOX,
    max_samples: int = 24,
) -> list[dict[str, object]]:
    height, width, _channels = final_rgba8.shape
    return build_default_probe_samples_for_size(width=width, height=height, wedge_bbox=wedge_bbox, max_samples=max_samples)


def build_default_probe_samples_for_size(
    *,
    width: int,
    height: int,
    wedge_bbox: tuple[float, float, float, float] = DEFAULT_WEDGE_BBOX,
    max_samples: int = 24,
) -> list[dict[str, object]]:
    sample_lookup: dict[tuple[int, int], dict[str, object]] = {}

    def add_sample(sample_id: str, x_value: int, y_value: int, classification: str, *tags: str) -> None:
        clamped_x = max(0, min(width - 1, int(x_value)))
        clamped_y = max(0, min(height - 1, int(y_value)))
        key = (clamped_x, clamped_y)
        if key in sample_lookup:
            return
        sample_lookup[key] = {
            "sample_id": sample_id,
            "x": clamped_x,
            "y": clamped_y,
            "classification": classification,
            "tags": list(tags or (classification,)),
            "screen_region": screen_region(width, height, clamped_x, clamped_y),
        }

    def normalized_to_pixel(x_fraction: float, y_fraction: float) -> tuple[int, int]:
        return (
            int(round(clamp01(x_fraction) * float(width - 1))),
            int(round(clamp01(y_fraction) * float(height - 1))),
        )

    x0, y0, x1, y1 = wedge_bbox
    for row_index in range(4):
        y_fraction = y0 + ((y1 - y0) * ((row_index + 0.5) / 4.0))
        for column_index in range(3):
            x_fraction = x0 + ((x1 - x0) * ((column_index + 0.5) / 3.0))
            x_value, y_value = normalized_to_pixel(x_fraction, y_fraction)
            add_sample(f"wedge_{row_index}_{column_index}", x_value, y_value, "wedge_interior", "wedge", "interior")

    edge_offsets = (-0.012, 0.012)
    edge_positions = (
        (x0, (y0 + y1) * 0.5, "vertical_left"),
        (x1, (y0 + y1) * 0.5, "vertical_right"),
        ((x0 + x1) * 0.5, y0, "horizontal_top"),
        ((x0 + x1) * 0.5, y1, "horizontal_bottom"),
    )
    for edge_x, edge_y, edge_name in edge_positions:
        for offset_index, offset in enumerate(edge_offsets):
            x_value, y_value = normalized_to_pixel(edge_x + offset if edge_name.startswith("vertical") else edge_x, edge_y + offset if edge_name.startswith("horizontal") else edge_y)
            classification = "wedge_edge_outside" if offset_index == 0 else "wedge_edge_inside"
            add_sample(f"{edge_name}_{offset_index}", x_value, y_value, classification, "wedge", "edge", edge_name)

    clean_positions = (
        (0.18, 0.22),
        (0.22, 0.74),
        (0.78, 0.20),
        (0.80, 0.72),
    )
    for clean_index, (x_fraction, y_fraction) in enumerate(clean_positions):
        x_value, y_value = normalized_to_pixel(x_fraction, y_fraction)
        add_sample(f"clean_{clean_index}", x_value, y_value, "clean_control", "clean", "control")

    boundary_positions = (
        (x0 - 0.02, y0 + 0.08),
        (x0 - 0.02, y1 - 0.08),
        (x1 + 0.02, y0 + 0.08),
        (x1 + 0.02, y1 - 0.08),
    )
    for boundary_index, (x_fraction, y_fraction) in enumerate(boundary_positions):
        x_value, y_value = normalized_to_pixel(x_fraction, y_fraction)
        add_sample(f"tile_boundary_{boundary_index}", x_value, y_value, "tile_boundary_adjacent", "boundary", "control")

    ordered_samples = list(sample_lookup.values())
    if max_samples > 0:
        ordered_samples = ordered_samples[: int(max_samples)]
    return ordered_samples


class IncrementalJsonReport:

    def __init__(self, path: Path) -> None:
        self.path = path
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._sample_index: dict[str, int] = {}
        self.data: dict[str, object] = {
            "events": [],
            "samples": [],
        }

    def mark(self, phase: str, **payload: object) -> None:
        event = {"phase": str(phase), **payload}
        events = self.data.setdefault("events", [])
        assert isinstance(events, list)
        events.append(event)
        self.data["last_phase"] = event
        self.write()

    def update_fields(self, **fields: object) -> None:
        self.data.update(fields)
        self.write()

    def upsert_sample(self, sample_id: str, **payload: object) -> None:
        samples = self.data.setdefault("samples", [])
        assert isinstance(samples, list)
        index = self._sample_index.get(sample_id)
        if index is None:
            index = len(samples)
            samples.append({"sample_id": sample_id})
            self._sample_index[sample_id] = index
        sample_record = samples[index]
        assert isinstance(sample_record, dict)
        sample_record.update(payload)
        self.write()

    def write(self) -> None:
        _write_json_file(self.path, self.data)


def write_progress_marker(path: Path, **payload: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    _write_json_file(path, payload)


def _write_json_file(path: Path, payload: object) -> None:
    temporary_path = path.with_suffix(path.suffix + ".tmp")
    encoded = json.dumps(payload, indent=2)
    try:
        temporary_path.write_text(encoded, encoding="utf-8")
        temporary_path.replace(path)
    except PermissionError:
        path.write_text(encoded, encoding="utf-8")
        if temporary_path.exists():
            temporary_path.unlink()


def summarize_model_mae(samples: list[dict[str, object]]) -> dict[str, object]:
    model_errors: dict[str, list[float]] = defaultdict(list)
    best_model_counts: Counter[str] = Counter()
    for sample in samples:
        raw_compare = sample.get("raw_tile_rgb_compare")
        if not isinstance(raw_compare, dict):
            continue
        for model_name, model_mae in raw_compare.get("sampling_model_mae", {}).items():
            model_errors[str(model_name)].append(float(model_mae))
        best_model_counts[str(raw_compare.get("best_model", "unknown"))] += 1
    return {
        "per_model": {
            model_name: {
                "sample_count": len(values),
                "median_mae": round(float(np.median(values)), 6) if values else None,
                "max_mae": round(float(np.max(values)), 6) if values else None,
                "best_fit_count": int(best_model_counts.get(model_name, 0)),
            }
            for model_name, values in sorted(model_errors.items())
        },
        "dominant_best_fit_model": best_model_counts.most_common(1)[0][0] if best_model_counts else "unknown",
    }