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

    def test_serialize_includes_reference_size_and_crop(self) -> None:
        layers = [_SampleLayer(layer_id="layer-1", shape="text", label="Alpha", x=100.0, y=200.0)]
        crop = {"shape": "rectangle", "mode": "include", "x0": 100.0, "y0": 50.0, "x1": 900.0, "y1": 450.0}
        payload = serialize_astrostack_overlay_preset(
            layers,
            reference_size=(1000, 500),
            crop=crop,
        )
        self.assertEqual(payload["version"], ASTROSTACK_PRESET_VERSION)
        self.assertEqual(payload["reference_size"], {"width": 1000, "height": 500})
        self.assertAlmostEqual(payload["crop"]["x0"], 0.1)
        self.assertAlmostEqual(payload["layers"][0]["x"], 0.1)
        self.assertAlmostEqual(payload["layers"][0]["y"], 0.4)

    def test_write_and_read_round_trip(self) -> None:
        layers = [
            _SampleLayer(layer_id="layer-1", shape="text", label="Alpha", x=10.0, y=20.0),
            _SampleLayer(layer_id="layer-2", shape="plot", label="SNR", x=30.0, y=40.0),
        ]
        crop = {"shape": "rectangle", "mode": "include", "x0": 10.0, "y0": 20.0, "x1": 90.0, "y1": 80.0}
        with tempfile.TemporaryDirectory() as temp_dir:
            preset_path = Path(temp_dir) / "preset.astrostack.json"
            write_astrostack_overlay_preset(
                preset_path,
                layers,
                reference_size=(100, 100),
                crop=crop,
            )
            payload = json.loads(preset_path.read_text(encoding="utf-8"))
            self.assertEqual(payload["kind"], ASTROSTACK_PRESET_KIND)
            preset = read_astrostack_overlay_preset(preset_path)
            self.assertEqual(preset.version, ASTROSTACK_PRESET_VERSION)
            self.assertEqual(preset.reference_size, (100, 100))
            materialized = materialize_astrostack_preset_layers(preset, (200, 200))
            self.assertAlmostEqual(materialized[0]["x"], 20.0)
            self.assertAlmostEqual(materialized[0]["y"], 40.0)
            materialized_crop = materialize_astrostack_preset_crop(preset, (200, 200))
            assert materialized_crop is not None
            self.assertAlmostEqual(materialized_crop["x0"], 20.0)
            self.assertAlmostEqual(materialized_crop["y1"], 160.0)

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
