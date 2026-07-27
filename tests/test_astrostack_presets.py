from __future__ import annotations

import json
import tempfile
import unittest
from dataclasses import dataclass
from pathlib import Path

from photometry_app.core.astrostack_presets import (
    ASTROSTACK_PRESET_KIND,
    ASTROSTACK_PRESET_VERSION,
    AstrostackOverlayPresetState,
    denormalize_astrostack_layer,
    materialize_astrostack_preset_crop,
    materialize_astrostack_preset_layers,
    materialize_astrostack_preset_region,
    normalize_astrostack_crop,
    normalize_astrostack_layer,
    read_astrostack_overlay_preset,
    serialize_astrostack_overlay_preset,
    write_astrostack_overlay_preset,
)


@dataclass(slots=True)
class _SampleLayer:
    layer_id: str
    shape: str
    label: str
    x: float
    y: float
    x2: float = 0.0
    y2: float = 0.0
    radius: float = 24.0
    text_size: float = 24.0
    plot_title_offset_x: float = 0.0
    plot_title_offset_y: float = 0.0
    plot_chart_margin_left: float = 0.0
    plot_chart_margin_top: float = 0.0


class AstrostackPresetTest(unittest.TestCase):
    def test_normalize_and_denormalize_layer_round_trip(self) -> None:
        layer = {
            "shape": "plot",
            "x": 800.0,
            "y": 120.0,
            "x2": 1200.0,
            "y2": 420.0,
            "radius": 48.0,
            "text_size": 24.0,
            "plot_title_offset_x": 40.0,
            "plot_title_offset_y": 20.0,
            "plot_chart_margin_left": 30.0,
            "plot_chart_margin_top": 15.0,
        }
        reference_size = (2000, 1000)
        target_size = (4000, 2000)
        normalized = normalize_astrostack_layer(layer, reference_size)
        restored = denormalize_astrostack_layer(normalized, reference_size, target_size)
        self.assertAlmostEqual(restored["x"], 1600.0)
        self.assertAlmostEqual(restored["y"], 240.0)
        self.assertAlmostEqual(restored["x2"], 2400.0)
        self.assertAlmostEqual(restored["y2"], 840.0)
        self.assertAlmostEqual(restored["radius"], 96.0)
        self.assertAlmostEqual(restored["text_size"], 48.0)
        self.assertAlmostEqual(restored["plot_title_offset_x"], 80.0)
        self.assertAlmostEqual(restored["plot_title_offset_y"], 40.0)
        self.assertAlmostEqual(restored["plot_chart_margin_left"], 60.0)
        self.assertAlmostEqual(restored["plot_chart_margin_top"], 30.0)

    def test_serialize_uses_canvas_and_source_sizes(self) -> None:
        layers = [_SampleLayer(layer_id="layer-1", shape="text", label="Alpha", x=100.0, y=50.0)]
        crop = {"shape": "rectangle", "mode": "include", "x0": 100.0, "y0": 50.0, "x1": 900.0, "y1": 450.0}
        payload = serialize_astrostack_overlay_preset(
            layers,
            canvas_size=(800, 400),
            source_size=(1000, 500),
            crop=crop,
            signal_region={"shape": "rectangle", "mode": "include", "x0": 40.0, "y0": 20.0, "x1": 120.0, "y1": 80.0},
        )
        self.assertEqual(payload["version"], ASTROSTACK_PRESET_VERSION)
        self.assertEqual(payload["coordinate_space"], "canvas")
        self.assertEqual(payload["canvas_size"], {"width": 800, "height": 400})
        self.assertEqual(payload["source_size"], {"width": 1000, "height": 500})
        self.assertAlmostEqual(payload["crop"]["x0"], 0.1)
        self.assertAlmostEqual(payload["layers"][0]["x"], 0.125)
        self.assertAlmostEqual(payload["layers"][0]["y"], 0.125)
        self.assertAlmostEqual(payload["signal_region"]["x0"], 0.05)

    def test_write_and_read_scales_layers_to_new_canvas(self) -> None:
        layers = [
            _SampleLayer(layer_id="layer-1", shape="text", label="Alpha", x=10.0, y=20.0),
            _SampleLayer(layer_id="layer-2", shape="plot", label="SNR", x=30.0, y=40.0, x2=90.0, y2=80.0),
        ]
        crop = {"shape": "rectangle", "mode": "include", "x0": 10.0, "y0": 20.0, "x1": 90.0, "y1": 80.0}
        with tempfile.TemporaryDirectory() as temp_dir:
            preset_path = Path(temp_dir) / "preset.astrostack.json"
            write_astrostack_overlay_preset(
                preset_path,
                layers,
                canvas_size=(100, 100),
                source_size=(200, 200),
                crop=crop,
            )
            payload = json.loads(preset_path.read_text(encoding="utf-8"))
            self.assertEqual(payload["kind"], ASTROSTACK_PRESET_KIND)
            preset = read_astrostack_overlay_preset(preset_path)
            self.assertEqual(preset.version, ASTROSTACK_PRESET_VERSION)
            self.assertEqual(preset.canvas_size, (100, 100))
            self.assertEqual(preset.source_size, (200, 200))
            materialized = materialize_astrostack_preset_layers(preset, (200, 200))
            self.assertAlmostEqual(materialized[0]["x"], 20.0)
            self.assertAlmostEqual(materialized[0]["y"], 40.0)
            self.assertAlmostEqual(materialized[1]["x2"], 180.0)
            materialized_crop = materialize_astrostack_preset_crop(preset, (400, 400))
            assert materialized_crop is not None
            self.assertAlmostEqual(materialized_crop["x0"], 20.0)
            self.assertAlmostEqual(materialized_crop["y1"], 160.0)

    def test_materialize_preserves_relative_layout_across_crop_aspect_change(self) -> None:
        # Saved on an 800x400 canvas; load onto a differently shaped 1200x900 canvas.
        layers = [
            _SampleLayer(
                layer_id="layer-1",
                shape="plot",
                label="SNR",
                x=560.0,
                y=240.0,
                x2=760.0,
                y2=380.0,
                radius=20.0,
            )
        ]
        payload = serialize_astrostack_overlay_preset(
            layers,
            canvas_size=(800, 400),
            source_size=(1600, 1200),
        )
        preset = AstrostackOverlayPresetState(
            version=int(payload["version"]),
            layers=tuple(payload["layers"]),
            reference_size=(800, 400),
            canvas_size=(800, 400),
            source_size=(1600, 1200),
        )
        materialized = materialize_astrostack_preset_layers(preset, (1200, 900))
        self.assertAlmostEqual(materialized[0]["x"], 840.0)
        self.assertAlmostEqual(materialized[0]["y"], 540.0)
        self.assertAlmostEqual(materialized[0]["x2"], 1140.0)
        self.assertAlmostEqual(materialized[0]["y2"], 855.0)

    def test_read_legacy_v1_preset_keeps_absolute_layers(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            preset_path = Path(temp_dir) / "legacy.json"
            preset_path.write_text(
                json.dumps(
                    {
                        "version": 1,
                        "kind": ASTROSTACK_PRESET_KIND,
                        "layers": [{"shape": "text", "label": "Alpha", "x": 12.0, "y": 34.0}],
                    }
                ),
                encoding="utf-8",
            )
            preset = read_astrostack_overlay_preset(preset_path)
            materialized = materialize_astrostack_preset_layers(preset, (1000, 1000))
            self.assertEqual(materialized[0]["x"], 12.0)
            self.assertEqual(materialized[0]["y"], 34.0)

    def test_read_legacy_v2_preset_uses_reference_size(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            preset_path = Path(temp_dir) / "v2.json"
            preset_path.write_text(
                json.dumps(
                    {
                        "version": 2,
                        "kind": ASTROSTACK_PRESET_KIND,
                        "reference_size": {"width": 100, "height": 100},
                        "layers": [{"shape": "text", "label": "Alpha", "x": 0.1, "y": 0.2}],
                        "crop": {
                            "shape": "rectangle",
                            "mode": "include",
                            "x0": 0.1,
                            "y0": 0.1,
                            "x1": 0.9,
                            "y1": 0.9,
                        },
                    }
                ),
                encoding="utf-8",
            )
            preset = read_astrostack_overlay_preset(preset_path)
            materialized = materialize_astrostack_preset_layers(preset, (200, 200))
            self.assertAlmostEqual(materialized[0]["x"], 20.0)
            self.assertAlmostEqual(materialized[0]["y"], 40.0)
            crop = materialize_astrostack_preset_crop(preset, (200, 200))
            assert crop is not None
            self.assertAlmostEqual(crop["x0"], 20.0)

    def test_materialize_v3_regions_are_canvas_relative(self) -> None:
        preset = AstrostackOverlayPresetState(
            version=3,
            layers=(),
            reference_size=(100, 50),
            canvas_size=(100, 50),
            source_size=(400, 200),
            signal_region={
                "shape": "rectangle",
                "mode": "include",
                "x0": 0.1,
                "y0": 0.2,
                "x1": 0.3,
                "y1": 0.4,
            },
        )
        region = materialize_astrostack_preset_region(
            preset.signal_region,
            preset=preset,
            target_size=(200, 100),
        )
        assert region is not None
        self.assertAlmostEqual(region["x0"], 20.0)
        self.assertAlmostEqual(region["y0"], 20.0)
        self.assertAlmostEqual(region["x1"], 60.0)
        self.assertAlmostEqual(region["y1"], 40.0)

    def test_read_rejects_invalid_preset(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            preset_path = Path(temp_dir) / "invalid.json"
            preset_path.write_text(json.dumps({"kind": "other"}), encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "not an Astrostack overlay preset"):
                read_astrostack_overlay_preset(preset_path)

    def test_normalize_crop(self) -> None:
        normalized = normalize_astrostack_crop(
            {"shape": "rectangle", "mode": "include", "x0": 100.0, "y0": 50.0, "x1": 500.0, "y1": 250.0},
            (1000, 500),
        )
        assert normalized is not None
        self.assertAlmostEqual(normalized["x1"], 0.5)


if __name__ == "__main__":
    unittest.main()
