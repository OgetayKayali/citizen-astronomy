from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from PySide6.QtGui import QColor, QImage

from photometry_app.core.animation_export import (
    StreamingGifWriter,
    StreamingMp4Writer,
    _mp4_aligned_dimension,
    _qimage_to_rgb_array,
    astrostack_gif_export_frame_indices,
    mp4_export_dependencies_available,
    resolve_astrostack_stack_export_frame_indices,
    resolve_sky_explorer_comparison_animation_frame_count,
    resolve_sky_explorer_comparison_animation_timing,
    resolve_ffmpeg_executable,
    sky_explorer_comparison_split_fractions,
    export_qimages_to_gif,
    export_qimages_to_gif_for_total_duration,
    export_qimages_to_mp4,
    export_qimages_to_mp4_for_total_duration,
)


class FakeStreamingGifWriter:
    last_instance: FakeStreamingGifWriter | None = None

    def __init__(self, output_path: Path, **kwargs: object) -> None:
        self.output_path = Path(output_path)
        self.kwargs = kwargs
        self.frames: list[QImage] = []
        FakeStreamingGifWriter.last_instance = self

    @property
    def frame_count(self) -> int:
        return len(self.frames)

    def __enter__(self) -> FakeStreamingGifWriter:
        return self

    def __exit__(self, *_args: object) -> bool:
        return False

    def append_qimage(self, image: QImage) -> None:
        self.frames.append(image)


FakeStreamingMp4Writer = FakeStreamingGifWriter


class AnimationExportTest(unittest.TestCase):
    def test_astrostack_gif_export_frame_indices_returns_all_frames_for_small_sets(self) -> None:
        self.assertEqual(astrostack_gif_export_frame_indices(3), (1, 2, 3))

    def test_astrostack_gif_export_frame_indices_subsamples_large_sets(self) -> None:
        indices = astrostack_gif_export_frame_indices(200, max_export_frames=60)
        self.assertEqual(indices[0], 1)
        self.assertEqual(indices[-1], 200)
        self.assertEqual(len(indices), 60)
        self.assertEqual(len(set(indices)), 60)

    def test_resolve_astrostack_stack_export_frame_indices_fast_mode_off_exports_every_stack(self) -> None:
        self.assertEqual(
            resolve_astrostack_stack_export_frame_indices(200, fast_mode=False),
            tuple(range(1, 201)),
        )

    def test_resolve_astrostack_stack_export_frame_indices_fast_mode_on_subsamples(self) -> None:
        indices = resolve_astrostack_stack_export_frame_indices(200, fast_mode=True, max_export_frames=60)
        self.assertEqual(len(indices), 60)
        self.assertEqual(indices[0], 1)
        self.assertEqual(indices[-1], 200)

    def test_mp4_aligned_dimension_rounds_up_to_h264_macroblock_size(self) -> None:
        self.assertEqual(_mp4_aligned_dimension(2518), 2528)
        self.assertEqual(_mp4_aligned_dimension(3792), 3792)
        self.assertEqual(_mp4_aligned_dimension(1534), 1536)

    def test_qimage_to_rgb_array_preserves_large_argb_frame_content(self) -> None:
        from PySide6.QtWidgets import QApplication

        app = QApplication.instance() or QApplication([])
        image = QImage(3792, 2518, QImage.Format.Format_RGB888)
        image.fill(QColor(80, 120, 200))
        image.setPixel(500, 500, QColor(255, 255, 255).rgb())
        composited = image.convertToFormat(QImage.Format.Format_ARGB32)

        rgb_array = _qimage_to_rgb_array(composited)

        self.assertEqual(rgb_array.shape, (2518, 3792, 3))
        self.assertGreater(float(rgb_array.mean()), 0.0)
        self.assertEqual(int(rgb_array[500, 500, 0]), 255)

    def test_resolve_sky_explorer_comparison_animation_frame_count_uses_duration(self) -> None:
        self.assertEqual(resolve_sky_explorer_comparison_animation_frame_count(5.0, fps=30.0), 150)
        self.assertEqual(resolve_sky_explorer_comparison_animation_frame_count(0.5, fps=30.0), 15)
        self.assertEqual(resolve_sky_explorer_comparison_animation_frame_count(5.0, fps=25.0), 125)

    def test_resolve_sky_explorer_comparison_animation_frame_count_smooth_motion_adds_frames(self) -> None:
        base = resolve_sky_explorer_comparison_animation_frame_count(2.0, fps=25.0)
        smooth = resolve_sky_explorer_comparison_animation_frame_count(
            2.0,
            fps=25.0,
            divider_travel_pixels=1000.0,
            smooth_motion=True,
        )
        self.assertEqual(base, 50)
        self.assertGreater(smooth, base)
        self.assertGreaterEqual(smooth, 481)

    def test_sky_explorer_comparison_split_fractions_moves_left_to_right(self) -> None:
        fractions = sky_explorer_comparison_split_fractions(frame_count=5, ping_pong=False)
        self.assertEqual(len(fractions), 5)
        self.assertAlmostEqual(fractions[0], 0.02, places=6)
        self.assertAlmostEqual(fractions[-1], 0.98, places=6)
        self.assertLess(fractions[0], fractions[1])
        self.assertLess(fractions[-2], fractions[-1])

    def test_sky_explorer_comparison_split_fractions_ping_pong_returns_to_start(self) -> None:
        fractions = sky_explorer_comparison_split_fractions(frame_count=5, ping_pong=True)
        self.assertEqual(len(fractions), 5)
        self.assertAlmostEqual(fractions[0], 0.02, places=6)
        self.assertAlmostEqual(fractions[-1], 0.02, places=6)
        self.assertAlmostEqual(max(fractions), 0.98, places=6)

    def test_resolve_sky_explorer_comparison_animation_timing_gif_mode_caps_frames(self) -> None:
        mp4_count, mp4_duration_ms = resolve_sky_explorer_comparison_animation_timing(
            3.0,
            fps=30.0,
            divider_travel_pixels=2000.0,
            smooth_motion=True,
            gif_mode=False,
        )
        gif_count, gif_duration_ms = resolve_sky_explorer_comparison_animation_timing(
            3.0,
            fps=30.0,
            divider_travel_pixels=2000.0,
            smooth_motion=True,
            gif_mode=True,
        )
        self.assertGreater(mp4_count, gif_count)
        self.assertLessEqual(gif_count, 150)
        self.assertGreaterEqual(gif_duration_ms, 20)
        self.assertAlmostEqual(gif_count * gif_duration_ms / 1000.0, 3.0, places=1)

    def test_resolve_sky_explorer_comparison_animation_timing_honors_total_duration(self) -> None:
        frame_count, frame_duration_ms = resolve_sky_explorer_comparison_animation_timing(
            5.0,
            fps=25.0,
            divider_travel_pixels=1000.0,
            smooth_motion=True,
        )
        self.assertGreater(frame_count, 125)
        self.assertLess(frame_duration_ms, 20)
        self.assertAlmostEqual(frame_count * frame_duration_ms / 1000.0, 5.0, places=1)

        short_count, short_duration_ms = resolve_sky_explorer_comparison_animation_timing(
            2.0,
            fps=25.0,
            divider_travel_pixels=1000.0,
            smooth_motion=True,
        )
        self.assertAlmostEqual(short_count * short_duration_ms / 1000.0, 2.0, places=2)
        self.assertLess(short_duration_ms, frame_duration_ms)

    def test_export_qimages_to_gif_for_total_duration_uses_short_frame_times(self) -> None:
        from PIL import Image, ImageSequence

        with tempfile.TemporaryDirectory() as temp_dir:
            output_path = Path(temp_dir) / "timed.gif"
            frames = []
            for index in range(100):
                frame = QImage(32, 32, QImage.Format.Format_ARGB32)
                frame.fill(QColor(index % 255, 64, 128))
                frames.append(frame)
            export_qimages_to_gif_for_total_duration(
                frames,
                output_path,
                total_duration_seconds=5.0,
                loop_count=1,
            )
            self.assertTrue(output_path.exists())
            self.assertGreater(output_path.stat().st_size, 0)
            with Image.open(output_path) as gif_image:
                durations = [
                    int(frame.info.get("duration", 0))
                    for frame in ImageSequence.Iterator(gif_image)
                ]
            self.assertEqual(len(durations), 100)
            self.assertAlmostEqual(sum(durations) / 1000.0, 5.0, places=1)

    def test_mp4_export_dependencies_available_returns_status_tuple(self) -> None:
        available, message = mp4_export_dependencies_available()
        self.assertIsInstance(available, bool)
        self.assertIsInstance(message, str)
        if not available:
            self.assertTrue(message)
        else:
            ffmpeg_path = Path(resolve_ffmpeg_executable())
            self.assertTrue(ffmpeg_path.is_file())

    def test_streaming_gif_writer_writes_animation_file(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            output_path = Path(temp_dir) / "stream.gif"
            dark_frame = QImage(48, 32, QImage.Format.Format_ARGB32)
            dark_frame.fill(QColor("black"))
            bright_frame = QImage(48, 32, QImage.Format.Format_ARGB32)
            bright_frame.fill(QColor("white"))

            with StreamingGifWriter(output_path, frame_duration_ms=120, loop_count=0, scale_percent=100) as writer:
                writer.append_qimage(dark_frame)
                writer.append_qimage(bright_frame)
                self.assertEqual(writer.frame_count, 2)

            self.assertTrue(output_path.exists())
            self.assertGreater(output_path.stat().st_size, 0)

    def test_export_qimages_to_gif_writes_animation_file(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            output_path = Path(temp_dir) / "blink.gif"
            dark_frame = QImage(48, 32, QImage.Format.Format_ARGB32)
            dark_frame.fill(QColor("black"))
            bright_frame = QImage(48, 32, QImage.Format.Format_ARGB32)
            bright_frame.fill(QColor("white"))

            export_qimages_to_gif([dark_frame, bright_frame], output_path, frame_duration_ms=120)

            self.assertTrue(output_path.exists())
            self.assertGreater(output_path.stat().st_size, 0)

    def test_export_qimages_to_mp4_writes_video_file(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            output_path = Path(temp_dir) / "blink.mp4"
            dark_frame = QImage(48, 32, QImage.Format.Format_ARGB32)
            dark_frame.fill(QColor("black"))
            bright_frame = QImage(48, 32, QImage.Format.Format_ARGB32)
            bright_frame.fill(QColor("white"))

            export_qimages_to_mp4([dark_frame, bright_frame], output_path, frame_duration_ms=120)

            self.assertTrue(output_path.exists())
            self.assertGreater(output_path.stat().st_size, 0)

    def test_export_qimages_to_mp4_accepts_odd_frame_dimensions(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            output_path = Path(temp_dir) / "odd-blink.mp4"
            odd_frame_a = QImage(1534, 1167, QImage.Format.Format_ARGB32)
            odd_frame_a.fill(QColor("black"))
            odd_frame_b = QImage(1534, 1167, QImage.Format.Format_ARGB32)
            odd_frame_b.fill(QColor("white"))

            export_qimages_to_mp4([odd_frame_a, odd_frame_b], output_path, frame_duration_ms=50)

            self.assertTrue(output_path.exists())
            self.assertGreater(output_path.stat().st_size, 0)


if __name__ == "__main__":
    unittest.main()