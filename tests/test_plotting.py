from __future__ import annotations

import tempfile
import unittest
from datetime import datetime, timedelta
from pathlib import Path
from unittest.mock import patch

import numpy as np

from photometry_app.core.models import LightCurvePoint, LightCurveSeries
from photometry_app.core.plotting import AnnotatedImageDisplay, AnnotatedImageRenderSettings, LightCurveFitConfig, _build_fit_curve, _periodic_fit_curve, build_annotated_image_display, build_light_curve_plot_payload, render_annotated_image


class AnnotatedImageDisplayTest(unittest.TestCase):
    def test_build_annotated_image_display_preserves_rgb_preview_data(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            image_path = Path(temp_dir) / "demo_rgb.xisf"
            image_path.write_text("placeholder", encoding="utf-8")
            rgb_data = np.array(
                [
                    [[1.0, 2.0, 3.0], [4.0, 5.0, 6.0]],
                    [[7.0, 8.0, 9.0], [10.0, 11.0, 12.0]],
                ],
                dtype=float,
            )

            with patch("photometry_app.core.plotting.read_image_data", return_value=rgb_data):
                display = build_annotated_image_display(image_path)

            self.assertEqual(display.normalized_data.shape, (2, 2))
            np.testing.assert_allclose(display.normalized_data, np.mean(rgb_data, axis=-1))
            assert display.color_preview_normalized is not None
            self.assertEqual(display.color_preview_normalized.shape, (2, 2, 3))

    def test_build_annotated_image_display_recommends_linear_for_jpeg_rgb(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            image_path = Path(temp_dir) / "demo_rgb.jpg"
            image_path.write_text("placeholder", encoding="utf-8")
            rgb_data = np.array(
                [
                    [[12, 24, 48], [96, 120, 144]],
                    [[180, 192, 220], [240, 248, 255]],
                ],
                dtype=np.uint8,
            )

            with patch("photometry_app.core.plotting.read_image_data", return_value=rgb_data):
                display = build_annotated_image_display(image_path)

        self.assertEqual(display.recommended_stretch_mode, "linear")

    def test_build_annotated_image_display_recommends_stf_for_float_science_frame(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            image_path = Path(temp_dir) / "demo.fit"
            image_path.write_text("placeholder", encoding="utf-8")
            data = np.zeros((32, 32), dtype=np.float32)
            data[15:17, 15:17] = 250.0

            with patch("photometry_app.core.plotting.read_image_data", return_value=data):
                display = build_annotated_image_display(image_path)

        self.assertEqual(display.recommended_stretch_mode, "stf")

    def test_render_annotated_image_stf_recovers_faint_structure_more_naturally_than_linear(self) -> None:
        image_path = Path("demo.fit")
        normalized_data = np.full((32, 32), 0.02, dtype=float)
        normalized_data[8:24, 8:24] = 0.045
        normalized_data[15:17, 15:17] = 0.9
        display = AnnotatedImageDisplay(
            image_path=image_path,
            normalized_data=normalized_data,
            norm=lambda data: np.asarray(data, dtype=float),
        )

        with patch("photometry_app.core.plotting._normalized_preview_data", return_value=normalized_data):
            linear = render_annotated_image(display, AnnotatedImageRenderSettings(stretch_mode="linear"))
            stf = render_annotated_image(display, AnnotatedImageRenderSettings(stretch_mode="stf"))

        self.assertGreater(int(stf[12, 12]), int(linear[12, 12]))
        self.assertLess(int(stf[0, 0]), 48)
        self.assertGreater(int(stf[15, 15]), 220)

    def test_build_annotated_image_display_caches_auto_stretch_preview(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            image_path = Path(temp_dir) / "demo.fit"
            image_path.write_text("placeholder", encoding="utf-8")
            data = np.full((64, 64), 512.0, dtype=np.float32)
            data[20:44, 20:44] = 900.0
            data[31:33, 31:33] = 6000.0

            with patch("photometry_app.core.plotting.read_image_data", return_value=data):
                display = build_annotated_image_display(image_path)

        self.assertIsNotNone(display.auto_stretch_preview)
        assert display.auto_stretch_preview is not None
        self.assertLess(int(np.median(display.auto_stretch_preview * 255.0)), 64)

    def test_render_annotated_image_returns_rgb_preview_for_color_input(self) -> None:
        image_path = Path("demo_rgb.xisf")
        display = AnnotatedImageDisplay(
            image_path=image_path,
            normalized_data=np.zeros((2, 2), dtype=float),
            norm=lambda data: np.asarray(data, dtype=float),
            color_preview_normalized=np.array(
                [
                    [[0.1, 0.2, 0.3], [0.4, 0.5, 0.6]],
                    [[0.7, 0.8, 0.9], [0.2, 0.3, 0.4]],
                ],
                dtype=float,
            ),
        )

        rendered = render_annotated_image(display, AnnotatedImageRenderSettings(stretch_mode="linear", brightness=0.0, contrast=1.0))

        self.assertEqual(rendered.shape, (2, 2, 3))
        self.assertEqual(rendered.dtype, np.uint8)

    def test_build_annotated_image_display_uses_native_linear_preview_for_uint16_rgb(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            image_path = Path(temp_dir) / "demo_rgb.tiff"
            image_path.write_text("placeholder", encoding="utf-8")
            rgb_data = np.array(
                [
                    [[0, 32768, 65535], [16384, 32768, 49152]],
                    [[8192, 16384, 24576], [4096, 2048, 1024]],
                ],
                dtype=np.uint16,
            )

            with patch("photometry_app.core.plotting.read_image_data", return_value=rgb_data):
                display = build_annotated_image_display(image_path)

        assert display.color_linear_preview_normalized is not None
        np.testing.assert_allclose(
            display.color_linear_preview_normalized,
            rgb_data.astype(np.float64) / 65535.0,
            atol=1e-6,
        )

    def test_render_annotated_image_prefers_native_linear_preview_for_uint16_rgb(self) -> None:
        image_path = Path("demo_rgb.tiff")
        display = AnnotatedImageDisplay(
            image_path=image_path,
            normalized_data=np.zeros((2, 2), dtype=float),
            norm=lambda data: np.asarray(data, dtype=float),
            color_preview_normalized=np.ones((2, 2, 3), dtype=float),
            color_linear_preview_normalized=np.array(
                [
                    [[0.0, 0.5, 1.0], [0.25, 0.5, 0.75]],
                    [[0.125, 0.25, 0.375], [0.0625, 0.03125, 0.015625]],
                ],
                dtype=float,
            ),
        )

        rendered = render_annotated_image(display, AnnotatedImageRenderSettings(stretch_mode="linear", brightness=0.0, contrast=1.0))

        np.testing.assert_array_equal(
            rendered,
            np.asarray(display.color_linear_preview_normalized * 255.0, dtype=np.uint8),
        )

    def test_render_annotated_image_applies_display_only_brightness_and_contrast(self) -> None:
        image_path = Path("demo_rgb.xisf")
        normalized_data = np.linspace(0.0, 1.0, 256, dtype=float).reshape(16, 16)
        display = AnnotatedImageDisplay(
            image_path=image_path,
            normalized_data=normalized_data,
            norm=lambda data: np.asarray(data, dtype=float),
        )

        with patch("photometry_app.core.plotting._normalized_preview_data", return_value=normalized_data):
            baseline = render_annotated_image(display, AnnotatedImageRenderSettings(stretch_mode="linear", brightness=0.0, contrast=1.0))
            brighter = render_annotated_image(display, AnnotatedImageRenderSettings(stretch_mode="linear", brightness=0.2, contrast=1.0))
            higher_contrast = render_annotated_image(display, AnnotatedImageRenderSettings(stretch_mode="linear", brightness=0.0, contrast=1.8))

        self.assertGreater(int(brighter[5, 5]), int(baseline[5, 5]))
        self.assertLess(int(higher_contrast[5, 5]), int(baseline[5, 5]))
        self.assertGreater(int(higher_contrast[10, 10]), int(baseline[10, 10]))

    def test_render_annotated_image_can_invert_display_output(self) -> None:
        image_path = Path("demo_rgb.xisf")
        normalized_data = np.linspace(0.0, 1.0, 16, dtype=float).reshape(4, 4)
        display = AnnotatedImageDisplay(
            image_path=image_path,
            normalized_data=normalized_data,
            norm=lambda data: np.asarray(data, dtype=float),
        )

        with patch("photometry_app.core.plotting._normalized_preview_data", return_value=normalized_data):
            baseline = render_annotated_image(display, AnnotatedImageRenderSettings(stretch_mode="linear"))
            inverted = render_annotated_image(display, AnnotatedImageRenderSettings(stretch_mode="linear", inverted=True))

        self.assertEqual(int(inverted[0, 0]), 255 - int(baseline[0, 0]))
        self.assertEqual(int(inverted[-1, -1]), 255 - int(baseline[-1, -1]))

    def test_render_annotated_image_supports_black_midtone_and_white_points(self) -> None:
        image_path = Path("demo.fit")
        normalized_data = np.linspace(0.0, 1.0, 25, dtype=float).reshape(5, 5)
        display = AnnotatedImageDisplay(
            image_path=image_path,
            normalized_data=normalized_data,
            norm=lambda data: np.asarray(data, dtype=float),
        )

        with patch("photometry_app.core.plotting._normalized_preview_data", return_value=normalized_data):
            rendered = render_annotated_image(
                display,
                AnnotatedImageRenderSettings(
                    stretch_mode="linear",
                    black_point=0.25,
                    midtone_point=0.5,
                    white_point=0.75,
                ),
            )

        self.assertEqual(int(rendered[0, 0]), 0)
        self.assertEqual(int(rendered[-1, -1]), 255)
        self.assertGreater(int(rendered[2, 2]), int(rendered[1, 2]))

    def test_render_annotated_image_applies_curve_points(self) -> None:
        image_path = Path("demo.fit")
        normalized_data = np.linspace(0.0, 1.0, 25, dtype=float).reshape(5, 5)
        display = AnnotatedImageDisplay(
            image_path=image_path,
            normalized_data=normalized_data,
            norm=lambda data: np.asarray(data, dtype=float),
        )

        with patch("photometry_app.core.plotting._normalized_preview_data", return_value=normalized_data):
            baseline = render_annotated_image(display, AnnotatedImageRenderSettings(stretch_mode="linear"))
            curved = render_annotated_image(
                display,
                AnnotatedImageRenderSettings(stretch_mode="linear", curve_points=((0.0, 0.0), (0.5, 0.2), (1.0, 1.0))),
            )

        self.assertLess(int(curved[2, 2]), int(baseline[2, 2]))
        self.assertEqual(int(curved[0, 0]), 0)
        self.assertEqual(int(curved[-1, -1]), 255)

    def test_asinh_render_darkens_sigma_clipped_background_noise(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            image_path = Path(temp_dir) / "demo.fit"
            image_path.write_text("placeholder", encoding="utf-8")
            data = np.full((128, 128), 100.0, dtype=float)
            data[::2, ::2] += 4.0
            data[1::2, 1::2] -= 4.0
            data[64, 64] = 180.0
            data[63, 65] = 165.0

            with patch("photometry_app.core.plotting.read_image_data", return_value=data):
                display = build_annotated_image_display(image_path)

            rendered = render_annotated_image(display, AnnotatedImageRenderSettings(stretch_mode="asinh"))

            self.assertLess(int(np.median(rendered)), 40)
            self.assertGreater(int(rendered[64, 64]), 220)

    def test_annotated_image_render_settings_default_to_auto_stretch(self) -> None:

        self.assertEqual(AnnotatedImageRenderSettings().stretch_mode, "stf")

    def test_build_annotated_image_display_caches_preview_transforms(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            image_path = Path(temp_dir) / "demo.fit"
            image_path.write_text("placeholder", encoding="utf-8")
            data = np.arange(64, dtype=float).reshape(8, 8)

            with patch("photometry_app.core.plotting.read_image_data", return_value=data):
                display = build_annotated_image_display(image_path)

        self.assertIsNotNone(display.preview_normalized)
        self.assertIsNotNone(display.asinh_preview)
        assert display.preview_normalized is not None
        assert display.asinh_preview is not None
        self.assertEqual(display.preview_normalized.shape, data.shape)
        self.assertEqual(display.asinh_preview.shape, data.shape)


class LightCurvePlotPayloadTest(unittest.TestCase):
    def test_payload_supports_calibrated_magnitude_axis(self) -> None:
        start = datetime(2025, 1, 1, 0, 0, 0)
        series = LightCurveSeries(
            object_name="Demo",
            source_id="target-1",
            source_name="Target",
            filter_name="CV",
            points=[
                LightCurvePoint(
                    observation_time=start,
                    file_path=Path("frame_01.fits"),
                    differential_magnitude=0.10,
                    instrumental_magnitude=-9.2,
                    flux=5000.0,
                    flux_error=15.0,
                    calibrated_magnitude=12.10,
                    calibrated_magnitude_error=0.03,
                    differential_magnitude_error=0.03,
                ),
                LightCurvePoint(
                    observation_time=start + timedelta(hours=6),
                    file_path=Path("frame_02.fits"),
                    differential_magnitude=0.25,
                    instrumental_magnitude=-9.0,
                    flux=4600.0,
                    flux_error=15.0,
                    calibrated_magnitude=12.25,
                    calibrated_magnitude_error=0.04,
                    differential_magnitude_error=0.04,
                ),
            ],
        )

        payload = build_light_curve_plot_payload(
            series,
            empty_message="No data available.",
            y_axis_mode="calibrated_magnitude",
        )

        self.assertEqual(payload.y_axis_label, "Calibrated Magnitude")
        self.assertTrue(payload.invert_y)
        self.assertEqual([point.y for point in payload.points], [12.10, 12.25])
        self.assertEqual([point.y_error for point in payload.points], [0.03, 0.04])

    def test_phase_payload_can_anchor_to_primary_minimum(self) -> None:
        start = datetime(2025, 1, 1, 0, 0, 0)
        series = LightCurveSeries(
            object_name="Demo",
            source_id="target-1",
            source_name="Target",
            filter_name="V",
            points=[
                LightCurvePoint(
                    observation_time=start,
                    file_path=Path("frame_01.fits"),
                    differential_magnitude=12.10,
                    instrumental_magnitude=None,
                    flux=None,
                    flux_error=None,
                    differential_magnitude_error=0.05,
                ),
                LightCurvePoint(
                    observation_time=start + timedelta(hours=6),
                    file_path=Path("frame_02.fits"),
                    differential_magnitude=12.55,
                    instrumental_magnitude=None,
                    flux=None,
                    flux_error=None,
                    differential_magnitude_error=0.05,
                ),
                LightCurvePoint(
                    observation_time=start + timedelta(hours=12),
                    file_path=Path("frame_03.fits"),
                    differential_magnitude=12.20,
                    instrumental_magnitude=None,
                    flux=None,
                    flux_error=None,
                    differential_magnitude_error=0.05,
                ),
            ],
        )

        payload = build_light_curve_plot_payload(
            series,
            empty_message="No data available.",
            x_axis_mode="phase",
            phase_period_hours=24.0,
            phase_anchor_mode="primary_minimum",
        )

        self.assertEqual(payload.x_axis_mode, "phase")
        self.assertAlmostEqual(payload.points[1].x, 0.0, places=6)
        self.assertAlmostEqual(payload.points[0].x, 0.75, places=6)

    def test_payload_suppresses_pathological_fit_curve(self) -> None:
        start = datetime(2025, 1, 1, 0, 0, 0)
        series = LightCurveSeries(
            object_name="Demo",
            source_id="target-1",
            source_name="Target",
            filter_name="V",
            points=[
                LightCurvePoint(
                    observation_time=start + timedelta(hours=index * 6),
                    file_path=Path(f"frame_{index + 1:02d}.fits"),
                    differential_magnitude=value,
                    instrumental_magnitude=None,
                    flux=None,
                    flux_error=None,
                    differential_magnitude_error=0.05,
                )
                for index, value in enumerate((0.1, -0.05, 0.08, -0.02))
            ],
        )

        def absurd_periodic_fit_curve(
            x_array: np.ndarray,
            y_array: np.ndarray,
            fit_x: np.ndarray,
            fit_config: LightCurveFitConfig,
            x_units: str,
            fit_weights: np.ndarray,
        ) -> tuple[np.ndarray, np.ndarray, int]:
            del x_array, y_array, fit_config, x_units, fit_weights
            return np.linspace(-6000.0, 1000.0, fit_x.size), np.zeros(4, dtype=float), 4

        with patch("photometry_app.core.plotting._periodic_fit_curve", side_effect=absurd_periodic_fit_curve):
            payload = build_light_curve_plot_payload(
                series,
                empty_message="No data available.",
                fit_config=LightCurveFitConfig(mode="periodic", periodic_period_hours=12.0, periodic_harmonics=2),
            )

        self.assertIsNone(payload.fit_x_values)
        self.assertIsNone(payload.fit_y_values)

    def test_periodic_fit_curve_reduces_harmonics_for_sparse_series(self) -> None:
        x_values = np.asarray([0.0, 0.55, 1.05, 1.6, 2.15, 2.7], dtype=float)
        y_values = np.asarray([12.10, 12.26, 12.04, 12.22, 12.08, 12.24], dtype=float)
        fit_x = np.linspace(float(np.min(x_values)), float(np.max(x_values)), 200)

        fit_y, model_y, parameter_count = _periodic_fit_curve(
            x_values,
            y_values,
            fit_x,
            LightCurveFitConfig(mode="periodic", periodic_period_hours=12.0, periodic_harmonics=4),
            "days",
            np.ones_like(x_values, dtype=float),
        )

        self.assertIsNotNone(fit_y)
        self.assertIsNotNone(model_y)
        self.assertGreater(parameter_count, 0)

    def test_phase_fit_curve_uses_full_fold_cycle(self) -> None:
        start = datetime(2025, 1, 1, 0, 0, 0)
        series = LightCurveSeries(
            object_name="Demo",
            source_id="target-1",
            source_name="Target",
            filter_name="V",
            points=[
                LightCurvePoint(
                    observation_time=start + timedelta(hours=offset_hours),
                    file_path=Path(f"frame_{index + 1:02d}.fits"),
                    differential_magnitude=value,
                    instrumental_magnitude=None,
                    flux=None,
                    flux_error=None,
                    differential_magnitude_error=0.03,
                )
                for index, (offset_hours, value) in enumerate(
                    (
                        (0.0, 12.00),
                        (4.0, 12.12),
                        (8.0, 12.05),
                        (23.0, 12.18),
                        (27.0, 12.02),
                        (31.0, 12.15),
                    )
                )
            ],
        )

        payload = build_light_curve_plot_payload(
            series,
            empty_message="No data available.",
            fit_config=LightCurveFitConfig(mode="periodic", periodic_period_hours=24.0, periodic_harmonics=2),
            x_axis_mode="phase",
            phase_period_hours=24.0,
            phase_anchor_mode="primary_minimum",
        )

        self.assertIsNotNone(payload.fit_x_values)
        self.assertIsNotNone(payload.fit_y_values)
        assert payload.fit_x_values is not None
        self.assertAlmostEqual(float(np.min(payload.fit_x_values)), 0.0, places=6)
        self.assertAlmostEqual(float(np.max(payload.fit_x_values)), 1.0, places=6)

    def test_periodic_fit_curve_increases_sampling_for_many_cycles(self) -> None:
        x_values = list(np.linspace(0.0, 3.0, 16))
        y_values = [12.0 + (0.15 * np.sin(2.0 * np.pi * value / 0.2)) for value in x_values]

        fit_x, fit_y = _build_fit_curve(
            x_values,
            y_values,
            [1.0] * len(x_values),
            LightCurveFitConfig(mode="periodic", periodic_period_hours=4.8, periodic_harmonics=2),
            y_axis_mode="differential_magnitude",
            x_units="days",
        )

        self.assertIsNotNone(fit_x)
        self.assertIsNotNone(fit_y)
        assert fit_x is not None
        self.assertGreater(fit_x.size, 400)

