from __future__ import annotations

from dataclasses import replace
from datetime import datetime
from pathlib import Path
import tempfile
import unittest
from unittest import mock

import numpy as np
from astropy.io import fits

from photometry_app.core.models import (
    FieldCatalog,
    LightCurvePoint,
    LightCurveSeries,
    PhotometryMeasurement,
    ProcessingReport,
)
from photometry_app.core.target_markers import TargetMarkerAppearance
from photometry_app.core.target_field_animation import (
    DEFAULT_TARGET_FIELD_ALIGN_MODE,
    DEFAULT_TARGET_FIELD_DURATION_SECONDS,
    DEFAULT_TARGET_FIELD_FOV_PX,
    DEFAULT_TARGET_FIELD_FPS,
    DEFAULT_TARGET_FIELD_SCALE_PERCENT,
    DEFAULT_TARGET_FIELD_STRETCH_MODE,
    TARGET_FIELD_ALIGN_ALIGN_THEN_CROP,
    TARGET_FIELD_ALIGN_CROP_THEN_ALIGN,
    TARGET_FIELD_ALIGN_NONE,
    TARGET_FIELD_PROGRESS_COMPOSE,
    TARGET_FIELD_PROGRESS_ENCODE,
    TARGET_FIELD_PROGRESS_NORMALIZE,
    TARGET_FIELD_PROGRESS_PREPARE,
    TargetFieldAnimationExportOptions,
    TargetFieldAnimationProgress,
    TargetFieldFrame,
    TargetFieldAnimationError,
    align_target_stamps,
    apply_target_field_marker,
    collect_target_field_frames,
    crop_comparison_scale_factors,
    crop_image_aligned_stamp,
    crop_target_centered_aligned_stamp,
    crop_target_stamp,
    estimate_alignment_from_star_positions,
    estimate_full_frame_alignment,
    estimate_stamp_alignment,
    estimate_stamp_alignments,
    estimate_stamp_background,
    export_target_field_animation,
    load_or_create_align_then_crop_stamp,
    load_or_create_full_aligned_stamp,
    load_or_create_target_stamp,
    _load_target_field_stamps_parallel,
    _prepare_target_field_frame,
    local_comparison_scale_factors,
    match_stamp_backgrounds,
    normalize_target_field_align_mode,
    normalize_target_field_duration_seconds,
    normalize_target_field_export_format,
    normalize_target_field_fov_px,
    normalize_target_field_fps,
    normalize_target_field_loop_count,
    normalize_target_field_marker_length_percent,
    normalize_target_field_marker_line_color,
    normalize_target_field_marker_line_width,
    normalize_target_field_marker_style,
    normalize_target_field_scale_percent,
    normalize_target_field_stretch_mode,
    render_target_field_marker_preview,
    resolve_target_field_parallel_workers,
    stretch_stamps_to_shared_display,
    target_field_duration_frame_ms,
    target_field_frame_duration_ms,
    target_field_marker_extents,
    target_field_progress_stage_title,
)


class TargetFieldAnimationTest(unittest.TestCase):
    def _measurement(self, *, file_path: Path, x: float, y: float, index: int) -> PhotometryMeasurement:
        return PhotometryMeasurement(
            source_id="vsx-demo",
            source_name="Demo Var",
            catalog="vsx",
            object_name="Demo",
            file_path=file_path,
            observation_time=datetime(2025, 2, 5, 20, 10 + index, 0),
            filter_name="V",
            ra_deg=10.0,
            dec_deg=20.0,
            x=x,
            y=y,
            flux=1000.0,
            flux_error=5.0,
            instrumental_magnitude=10.0,
            differential_magnitude=12.0 + (index * 0.1),
            is_variable=True,
            is_reference=False,
        )

    def test_normalize_fov_uses_default_and_clamps(self) -> None:
        self.assertEqual(normalize_target_field_fov_px(None), DEFAULT_TARGET_FIELD_FOV_PX)
        self.assertEqual(normalize_target_field_fov_px(250), 250)
        self.assertEqual(normalize_target_field_fov_px(8), 32)
        self.assertEqual(normalize_target_field_fov_px(9000), 2000)

    def test_normalize_fps_uses_default_and_clamps(self) -> None:
        self.assertEqual(normalize_target_field_fps(None), DEFAULT_TARGET_FIELD_FPS)
        self.assertEqual(normalize_target_field_fps(12), 12.0)
        self.assertEqual(normalize_target_field_fps(0.2), 1.0)
        self.assertEqual(normalize_target_field_fps(80), 30.0)
        self.assertEqual(target_field_frame_duration_ms(12), 83)
        self.assertEqual(target_field_frame_duration_ms(30), 33)

    def test_normalize_duration_scale_align_and_format(self) -> None:
        self.assertEqual(normalize_target_field_duration_seconds(None), DEFAULT_TARGET_FIELD_DURATION_SECONDS)
        self.assertEqual(normalize_target_field_duration_seconds(8), 8.0)
        self.assertEqual(normalize_target_field_duration_seconds(0.1), 0.5)
        self.assertEqual(normalize_target_field_duration_seconds(400), 120.0)
        self.assertEqual(normalize_target_field_loop_count(None), 1)
        self.assertEqual(normalize_target_field_loop_count(3), 3)
        self.assertEqual(normalize_target_field_loop_count(0), 1)
        self.assertEqual(normalize_target_field_loop_count(99), 20)
        self.assertEqual(TargetFieldAnimationExportOptions().normalized().loop_count, 1)
        self.assertEqual(normalize_target_field_scale_percent(None), DEFAULT_TARGET_FIELD_SCALE_PERCENT)
        self.assertEqual(normalize_target_field_scale_percent(150), 150)
        self.assertEqual(normalize_target_field_scale_percent(5), 10)
        self.assertEqual(normalize_target_field_scale_percent(500), 200)
        self.assertEqual(normalize_target_field_align_mode(None), DEFAULT_TARGET_FIELD_ALIGN_MODE)
        self.assertEqual(normalize_target_field_align_mode(True), TARGET_FIELD_ALIGN_CROP_THEN_ALIGN)
        self.assertEqual(normalize_target_field_align_mode(False), TARGET_FIELD_ALIGN_NONE)
        self.assertEqual(normalize_target_field_align_mode("align_then_crop"), TARGET_FIELD_ALIGN_ALIGN_THEN_CROP)
        self.assertEqual(normalize_target_field_export_format(".mp4"), "mp4")
        self.assertEqual(normalize_target_field_export_format("nope"), "gif")
        self.assertEqual(normalize_target_field_marker_style(None), "none")
        self.assertEqual(normalize_target_field_marker_style("pointer"), "pointer")
        self.assertEqual(normalize_target_field_marker_style("nope"), "none")
        self.assertEqual(normalize_target_field_marker_length_percent(None), 36)
        self.assertEqual(normalize_target_field_marker_length_percent(12), 12)
        self.assertEqual(normalize_target_field_marker_length_percent(3), 10)
        self.assertEqual(normalize_target_field_marker_line_width(2.5), 2.5)
        self.assertEqual(normalize_target_field_marker_line_color("#00ff00"), "#00ff00")
        self.assertEqual(TargetFieldAnimationExportOptions().normalized().marker_style, "none")
        self.assertEqual(TargetFieldAnimationExportOptions().normalized().marker_length_percent, 36)
        self.assertEqual(target_field_duration_frame_ms(8.0, 10, gif=False), 800)
        self.assertEqual(target_field_duration_frame_ms(0.5, 100, gif=True), 20)
        self.assertEqual(
            TargetFieldAnimationExportOptions().normalized().align_mode,
            TARGET_FIELD_ALIGN_CROP_THEN_ALIGN,
        )

    def test_progress_stage_titles_follow_align_mode_and_format(self) -> None:
        self.assertEqual(
            target_field_progress_stage_title(TARGET_FIELD_PROGRESS_PREPARE),
            "Crop, then align",
        )
        self.assertEqual(
            target_field_progress_stage_title(
                TARGET_FIELD_PROGRESS_PREPARE,
                align_mode=TARGET_FIELD_ALIGN_ALIGN_THEN_CROP,
            ),
            "Align, then crop",
        )
        self.assertEqual(
            target_field_progress_stage_title(
                TARGET_FIELD_PROGRESS_PREPARE,
                align_mode=TARGET_FIELD_ALIGN_NONE,
            ),
            "Crop frames",
        )
        self.assertEqual(target_field_progress_stage_title(TARGET_FIELD_PROGRESS_NORMALIZE), "Normalize & stretch")
        self.assertEqual(target_field_progress_stage_title(TARGET_FIELD_PROGRESS_COMPOSE), "Compose frames")
        self.assertEqual(target_field_progress_stage_title(TARGET_FIELD_PROGRESS_ENCODE, export_format="mp4"), "Encode MP4")

    def test_align_locks_shifted_star_to_reference(self) -> None:
        reference = np.zeros((40, 40), dtype=float)
        reference[19:22, 19:22] = 20.0
        reference[20, 20] = 80.0
        shifted = np.zeros((40, 40), dtype=float)
        shifted[21:24, 16:19] = 20.0
        shifted[22, 17] = 80.0

        aligned = align_target_stamps([reference, shifted])
        peak_y, peak_x = np.unravel_index(int(np.nanargmax(aligned[1])), aligned[1].shape)

        self.assertEqual(aligned[0].shape, (40, 40))
        self.assertLessEqual(abs(int(peak_y) - 20), 1)
        self.assertLessEqual(abs(int(peak_x) - 20), 1)

    def test_align_corrects_meridian_flip(self) -> None:
        reference = np.full((48, 48), 8.0)
        reference[19:22, 19:22] = 40.0
        reference[20, 20] = 90.0
        reference[11:14, 27:30] = 28.0
        reference[12, 28] = 70.0
        flipped = np.rot90(reference, 2)

        solution = estimate_stamp_alignment(reference, flipped)
        aligned = align_target_stamps([reference, flipped])
        companion = aligned[1][10:15, 26:31]

        self.assertEqual(solution.orientation, "rot180")
        self.assertLessEqual(abs(int(np.unravel_index(int(np.nanargmax(companion)), companion.shape)[0]) + 10 - 12), 2)
        self.assertLessEqual(abs(int(np.unravel_index(int(np.nanargmax(companion)), companion.shape)[1]) + 26 - 28), 2)

    def test_align_keeps_identity_for_subpixel_shift(self) -> None:
        yy, xx = np.mgrid[0:40, 0:40]
        reference = 12.0 * np.exp(-((yy - 20.0) ** 2 + (xx - 20.0) ** 2) / 2.2)
        try:
            from scipy import ndimage
        except ImportError:
            self.skipTest("scipy is required for subpixel alignment")
        shifted = ndimage.shift(reference, shift=(0.7, -0.9), order=3, mode="constant")

        solution = estimate_stamp_alignment(reference, shifted)

        self.assertEqual(solution.orientation, "identity")
        self.assertAlmostEqual(solution.shift_y, -0.7, delta=0.35)
        self.assertAlmostEqual(solution.shift_x, 0.9, delta=0.35)

    def test_align_does_not_flicker_on_a_wide_shifted_field(self) -> None:
        rng = np.random.default_rng(4)
        reference = np.full((120, 120), 10.0)
        for _ in range(28):
            row = int(rng.integers(8, 112))
            column = int(rng.integers(8, 112))
            reference[row - 1 : row + 2, column - 1 : column + 2] += 18.0
            reference[row, column] += 45.0
        try:
            from scipy import ndimage
        except ImportError:
            self.skipTest("scipy is required for subpixel alignment")
        stamps = [reference]
        for shift in ((1.2, -0.8), (0.4, 1.1), (-0.9, 0.6), (1.5, 0.2), (-0.3, -1.0)):
            stamps.append(ndimage.shift(reference, shift=shift, order=3, mode="nearest"))

        solutions = estimate_stamp_alignments(stamps)

        self.assertEqual([item.orientation for item in solutions], ["identity"] * len(stamps))

    def test_align_keeps_a_real_meridian_flip_sticky(self) -> None:
        reference = np.full((64, 64), 8.0)
        reference[30:34, 30:34] = 40.0
        reference[32, 32] = 90.0
        reference[12:15, 44:47] = 28.0
        reference[13, 45] = 70.0
        reference[48:51, 18:21] = 22.0
        reference[49, 19] = 55.0
        flipped = np.rot90(reference, 2)
        stamps = [reference, reference, flipped, flipped, flipped]

        solutions = estimate_stamp_alignments(stamps)

        self.assertEqual(
            [item.orientation for item in solutions],
            ["identity", "identity", "rot180", "rot180", "rot180"],
        )

    def test_full_frame_align_then_crop_locks_a_shifted_field(self) -> None:
        reference = np.full((80, 90), 8.0)
        reference[29:32, 39:42] = 40.0
        reference[30, 40] = 90.0
        reference[14:17, 54:57] = 25.0
        reference[15, 55] = 70.0
        try:
            from scipy import ndimage
        except ImportError:
            self.skipTest("scipy is required for full-frame alignment")
        shifted = ndimage.shift(reference, shift=(2.0, -3.0), order=1, mode="nearest")

        solution = estimate_full_frame_alignment(reference, shifted)
        reference_crop = crop_target_stamp(reference, 40.0, 30.0, 25)
        aligned_crop = crop_image_aligned_stamp(shifted, solution, center_x=40.0, center_y=30.0, fov_px=25)

        self.assertEqual(solution.orientation, "identity")
        self.assertLessEqual(abs(int(np.unravel_index(int(np.nanargmax(aligned_crop)), aligned_crop.shape)[0]) - int(np.unravel_index(int(np.nanargmax(reference_crop)), reference_crop.shape)[0])), 1)
        self.assertLessEqual(abs(int(np.unravel_index(int(np.nanargmax(aligned_crop)), aligned_crop.shape)[1]) - int(np.unravel_index(int(np.nanargmax(reference_crop)), reference_crop.shape)[1])), 1)

    def test_full_frame_align_then_crop_corrects_meridian_flip(self) -> None:
        reference = np.full((80, 90), 8.0)
        reference[29:32, 39:42] = 40.0
        reference[30, 40] = 90.0
        reference[14:17, 54:57] = 25.0
        reference[15, 55] = 70.0
        reference[50:53, 18:21] = 22.0
        reference[51, 19] = 60.0
        flipped = np.rot90(reference, 2)

        solution = estimate_full_frame_alignment(reference, flipped)
        reference_crop = crop_target_stamp(reference, 40.0, 30.0, 25)
        aligned_crop = crop_image_aligned_stamp(flipped, solution, center_x=40.0, center_y=30.0, fov_px=25)
        companion_ref = np.unravel_index(int(np.nanargmax(reference_crop[0:10, 14:22])), (10, 8))
        companion_aligned = np.unravel_index(int(np.nanargmax(aligned_crop[0:10, 14:22])), (10, 8))

        self.assertEqual(solution.orientation, "rot180")
        self.assertLessEqual(abs(int(companion_aligned[0]) - int(companion_ref[0])), 2)
        self.assertLessEqual(abs(int(companion_aligned[1]) - int(companion_ref[1])), 2)

        flipped_target_x = (90 - 1) - 40.0
        flipped_target_y = (80 - 1) - 30.0
        centered = crop_target_centered_aligned_stamp(
            flipped,
            solution,
            target_x=flipped_target_x,
            target_y=flipped_target_y,
            fov_px=25,
        )
        peak_y, peak_x = np.unravel_index(int(np.nanargmax(centered)), centered.shape)
        self.assertLessEqual(abs(int(peak_y) - 12), 1)
        self.assertLessEqual(abs(int(peak_x) - 12), 1)

    def test_star_positions_choose_identity_for_a_shift_and_rot180_for_a_flip(self) -> None:
        reference = {
            "target": (40.0, 30.0),
            "comp-a": (55.0, 15.0),
            "comp-b": (19.0, 51.0),
        }
        shifted = {key: (x - 3.0, y + 2.0) for key, (x, y) in reference.items()}
        flipped = {key: ((90 - 1) - x, (80 - 1) - y) for key, (x, y) in reference.items()}

        shift_solution = estimate_alignment_from_star_positions(reference, shifted, (80, 90))
        flip_solution = estimate_alignment_from_star_positions(reference, flipped, (80, 90))
        single = estimate_alignment_from_star_positions({"target": (40.0, 30.0)}, {"target": (49.0, 49.0)}, (80, 90))

        self.assertIsNotNone(shift_solution)
        self.assertIsNotNone(flip_solution)
        assert shift_solution is not None
        assert flip_solution is not None
        self.assertEqual(shift_solution.orientation, "identity")
        self.assertAlmostEqual(shift_solution.shift_x, 3.0, places=5)
        self.assertAlmostEqual(shift_solution.shift_y, -2.0, places=5)
        self.assertEqual(flip_solution.orientation, "rot180")
        self.assertIsNotNone(single)
        assert single is not None
        self.assertEqual(single.orientation, "identity")

    def test_star_positions_choose_flip_lr_and_keep_field_handedness(self) -> None:
        reference = np.full((80, 90), 8.0)
        reference[29:32, 39:42] = 40.0
        reference[30, 40] = 90.0
        reference[14:17, 54:57] = 25.0
        reference[15, 55] = 70.0
        reference[50:53, 18:21] = 22.0
        reference[51, 19] = 60.0
        mirrored = np.fliplr(reference)
        positions = {
            "target": (40.0, 30.0),
            "comp-a": (55.0, 15.0),
            "comp-b": (19.0, 51.0),
        }
        mirrored_positions = {key: ((90 - 1) - x, y) for key, (x, y) in positions.items()}

        solution = estimate_alignment_from_star_positions(positions, mirrored_positions, (80, 90))
        sticky = estimate_alignment_from_star_positions(
            positions,
            mirrored_positions,
            (80, 90),
            previous_orientation="flip_lr",
        )
        detected = estimate_full_frame_alignment(reference, mirrored)

        self.assertIsNotNone(solution)
        assert solution is not None
        self.assertEqual(solution.orientation, "flip_lr")
        self.assertIsNotNone(sticky)
        assert sticky is not None
        self.assertEqual(sticky.orientation, "flip_lr")
        self.assertEqual(detected.orientation, "flip_lr")

        fov_px = 51
        reference_crop = crop_target_stamp(reference, 40.0, 30.0, fov_px)
        centered = crop_target_centered_aligned_stamp(
            mirrored,
            solution,
            target_x=mirrored_positions["target"][0],
            target_y=mirrored_positions["target"][1],
            fov_px=fov_px,
        )

        def companion_offset(stamp: np.ndarray) -> tuple[int, int]:
            masked = np.asarray(stamp, dtype=float).copy()
            center_y, center_x = masked.shape[0] // 2, masked.shape[1] // 2
            masked[center_y - 3 : center_y + 4, center_x - 3 : center_x + 4] = 0.0
            peak_y, peak_x = np.unravel_index(int(np.nanargmax(masked)), masked.shape)
            return int(peak_y) - center_y, int(peak_x) - center_x

        reference_offset = companion_offset(reference_crop)
        aligned_offset = companion_offset(centered)
        self.assertEqual(int(np.sign(aligned_offset[0])), int(np.sign(reference_offset[0])))
        self.assertEqual(int(np.sign(aligned_offset[1])), int(np.sign(reference_offset[1])))

    def test_star_positions_choose_flip_ud_for_an_up_down_mirror(self) -> None:
        reference = {
            "target": (40.0, 30.0),
            "comp-a": (55.0, 15.0),
            "comp-b": (19.0, 51.0),
        }
        mirrored = {key: (x, (80 - 1) - y) for key, (x, y) in reference.items()}

        solution = estimate_alignment_from_star_positions(reference, mirrored, (80, 90))

        self.assertIsNotNone(solution)
        assert solution is not None
        self.assertEqual(solution.orientation, "flip_ud")

    def test_star_positions_accept_clear_rot180_with_wcs_residuals(self) -> None:
        image_shape = (1000, 1200)
        reference = {
            "star-1": (120.0, 140.0),
            "star-2": (290.0, 760.0),
            "star-3": (430.0, 330.0),
            "star-4": (570.0, 880.0),
            "star-5": (710.0, 190.0),
            "star-6": (860.0, 610.0),
            "star-7": (1010.0, 420.0),
            "star-8": (1090.0, 820.0),
        }
        residuals = (
            (-6.0, -2.0),
            (-4.0, 3.0),
            (-2.0, -5.0),
            (1.0, 6.0),
            (3.0, -4.0),
            (5.0, 2.0),
            (7.0, 5.0),
            (-7.0, 4.0),
        )
        height, width = image_shape
        flipped = {
            source_id: (
                (width - 1) - (x + residual_x),
                (height - 1) - (y + residual_y),
            )
            for (source_id, (x, y)), (residual_x, residual_y) in zip(
                reference.items(),
                residuals,
                strict=True,
            )
        }

        solution = estimate_alignment_from_star_positions(reference, flipped, image_shape)

        self.assertIsNotNone(solution)
        assert solution is not None
        self.assertEqual(solution.orientation, "rot180")

    def test_target_centered_crop_is_not_moved_by_post_flip_field_residuals(self) -> None:
        reference = np.full((100, 100), 10.0)
        reference[49:52, 49:52] = 200.0
        for x, y in ((35, 35), (65, 35), (35, 65), (65, 65)):
            reference[y - 1 : y + 2, x - 1 : x + 2] = 500.0

        oriented_source = np.full_like(reference, 10.0)
        oriented_source[49:52, 49:52] = 200.0
        for x, y in ((38, 35), (68, 35), (38, 65), (68, 65)):
            oriented_source[y - 1 : y + 2, x - 1 : x + 2] = 500.0
        source = np.rot90(oriented_source, 2)

        reference_positions = {
            "target": (50.0, 50.0),
            "comp-a": (35.0, 35.0),
            "comp-b": (65.0, 35.0),
            "comp-c": (35.0, 65.0),
        }
        source_positions = {
            source_id: ((100 - 1) - x, (100 - 1) - y)
            for source_id, (x, y) in reference_positions.items()
        }

        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            reference_path = root / "reference.fits"
            source_path = root / "source.fits"
            fits.PrimaryHDU(reference).writeto(reference_path)
            fits.PrimaryHDU(source).writeto(source_path)
            reference_measurement = self._measurement(file_path=reference_path, x=50.0, y=50.0, index=0)
            source_measurement = self._measurement(file_path=source_path, x=49.0, y=49.0, index=1)
            reference_crop = crop_target_stamp(reference, 50.0, 50.0, 61)

            stamp, orientation = load_or_create_full_aligned_stamp(
                source_measurement,
                reference_measurement=reference_measurement,
                reference_image=reference,
                fov_px=61,
                cache_dir=None,
                source_id="vsx-demo",
                reference_positions=reference_positions,
                source_positions=source_positions,
                reference_crop=reference_crop,
            )

        center = stamp.shape[0] // 2
        local = stamp[center - 4 : center + 5, center - 4 : center + 5]
        local_y, local_x = np.unravel_index(int(np.nanargmax(local)), local.shape)
        self.assertEqual(orientation, "rot180")
        self.assertLessEqual(abs((center - 4 + int(local_y)) - center), 1)
        self.assertLessEqual(abs((center - 4 + int(local_x)) - center), 1)

    def test_export_path_crops_before_image_alignment(self) -> None:
        reference = np.full((100, 120), 8.0)
        reference[49:52, 59:62] = 40.0
        reference[50, 60] = 90.0
        reference[29:32, 74:77] = 25.0
        reference[30, 75] = 70.0
        reference[69:72, 34:37] = 22.0
        reference[70, 35] = 60.0
        flipped = np.rot90(reference, 2)

        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            reference_path = root / "reference.fits"
            flipped_path = root / "flipped.fits"
            fits.PrimaryHDU(reference).writeto(reference_path)
            fits.PrimaryHDU(flipped).writeto(flipped_path)
            reference_measurement = self._measurement(file_path=reference_path, x=60.0, y=50.0, index=0)
            flipped_measurement = self._measurement(file_path=flipped_path, x=59.0, y=49.0, index=1)
            reference_crop = crop_target_stamp(reference, 60.0, 50.0, 81)

            with mock.patch(
                "photometry_app.core.target_field_animation.estimate_full_frame_alignment",
                wraps=estimate_full_frame_alignment,
            ) as alignment:
                stamp, orientation = load_or_create_full_aligned_stamp(
                    flipped_measurement,
                    reference_measurement=reference_measurement,
                    reference_image=reference,
                    fov_px=81,
                    cache_dir=None,
                    source_id="vsx-demo",
                    reference_crop=reference_crop,
                )

        alignment.assert_called_once()
        aligned_reference, aligned_source = alignment.call_args.args[:2]
        self.assertEqual(aligned_reference.shape, (81, 81))
        self.assertEqual(aligned_source.shape, (81, 81))
        self.assertEqual(stamp.shape, (81, 81))
        self.assertEqual(orientation, "rot180")

    def test_align_then_crop_path_aligns_full_frames_before_crop(self) -> None:
        reference = np.full((100, 100), 8.0)
        reference[49:52, 59:62] = 40.0
        reference[50, 60] = 90.0
        reference[29:32, 74:77] = 25.0
        reference[30, 75] = 70.0
        reference[69:72, 34:37] = 22.0
        reference[70, 35] = 60.0
        flipped = np.rot90(reference, 2)

        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            reference_path = root / "reference.fits"
            flipped_path = root / "flipped.fits"
            fits.PrimaryHDU(reference).writeto(reference_path)
            fits.PrimaryHDU(flipped).writeto(flipped_path)
            reference_measurement = self._measurement(file_path=reference_path, x=60.0, y=50.0, index=0)
            flipped_measurement = self._measurement(file_path=flipped_path, x=39.0, y=49.0, index=1)

            with mock.patch(
                "photometry_app.core.target_field_animation.estimate_full_frame_alignment",
                wraps=estimate_full_frame_alignment,
            ) as alignment:
                stamp, orientation = load_or_create_align_then_crop_stamp(
                    flipped_measurement,
                    reference_measurement=reference_measurement,
                    reference_image=reference,
                    fov_px=81,
                    cache_dir=None,
                    source_id="vsx-demo",
                )

        alignment.assert_called_once()
        aligned_reference, aligned_source = alignment.call_args.args[:2]
        self.assertEqual(aligned_reference.shape, (100, 100))
        self.assertEqual(aligned_source.shape, (100, 100))
        self.assertEqual(stamp.shape, (81, 81))
        self.assertEqual(orientation, "rot180")

    def test_export_dispatches_gif_or_mp4_with_duration_and_scale(self) -> None:
        from PySide6.QtGui import QImage
        from PySide6.QtWidgets import QApplication

        if QApplication.instance() is None:
            QApplication([])

        reference = np.full((40, 40), 12.0)
        reference[18:23, 18:23] = 80.0
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            image_path = root / "frame.fits"
            fits.PrimaryHDU(reference).writeto(image_path)
            measurement = self._measurement(file_path=image_path, x=20.0, y=20.0, index=0)
            series = LightCurveSeries(
                object_name="Demo",
                source_id="vsx-demo",
                source_name="Demo Var",
                filter_name="V",
                points=[
                    LightCurvePoint(
                        observation_time=measurement.observation_time,
                        file_path=measurement.file_path,
                        differential_magnitude=12.0,
                        instrumental_magnitude=10.0,
                        flux=1000.0,
                        flux_error=5.0,
                    )
                ],
            )
            report = ProcessingReport(
                object_name="Demo",
                files_processed=1,
                solved_files=1,
                field_catalog=FieldCatalog(center_ra_deg=10.0, center_dec_deg=20.0, radius_deg=1.0),
                measurements=[measurement],
                light_curves=[series],
            )
            gif_path = root / "target_field.gif"
            mp4_path = root / "target_field.mp4"
            dummy = QImage(8, 8, QImage.Format.Format_RGB888)
            dummy.fill(0)
            progress_events: list[TargetFieldAnimationProgress] = []

            with (
                mock.patch(
                    "photometry_app.core.target_field_animation.export_qimages_to_gif",
                ) as gif_export,
                mock.patch(
                    "photometry_app.core.target_field_animation.export_qimages_to_mp4",
                ) as mp4_export,
                mock.patch(
                    "photometry_app.core.target_field_animation._render_light_curve_payload_with_highlight",
                    return_value=dummy,
                ),
            ):
                export_target_field_animation(
                    report,
                    "vsx-demo",
                    gif_path,
                    fov_px=21,
                    align_mode=TARGET_FIELD_ALIGN_NONE,
                    duration_seconds=4.0,
                    scale_percent=50,
                    export_format="gif",
                    series=series,
                    progress_callback=progress_events.append,
                )
                export_target_field_animation(
                    report,
                    "vsx-demo",
                    mp4_path,
                    fov_px=21,
                    align_mode=TARGET_FIELD_ALIGN_NONE,
                    duration_seconds=4.0,
                    loop_count=3,
                    scale_percent=50,
                    export_format="mp4",
                    series=series,
                )

        gif_export.assert_called_once()
        mp4_export.assert_called_once()
        self.assertEqual(gif_export.call_args.kwargs["frame_duration_ms"], 4000)
        self.assertEqual(gif_export.call_args.kwargs["scale_percent"], 50)
        self.assertEqual(gif_export.call_args.kwargs["loop_count"], 0)
        self.assertEqual(mp4_export.call_args.kwargs["frame_duration_ms"], 4000)
        self.assertEqual(mp4_export.call_args.kwargs["scale_percent"], 50)
        self.assertEqual(mp4_export.call_args.kwargs["repeat_count"], 3)
        self.assertEqual(
            [event.stage for event in progress_events],
            [
                TARGET_FIELD_PROGRESS_PREPARE,
                TARGET_FIELD_PROGRESS_PREPARE,
                TARGET_FIELD_PROGRESS_NORMALIZE,
                TARGET_FIELD_PROGRESS_NORMALIZE,
                TARGET_FIELD_PROGRESS_COMPOSE,
                TARGET_FIELD_PROGRESS_COMPOSE,
                TARGET_FIELD_PROGRESS_ENCODE,
                TARGET_FIELD_PROGRESS_ENCODE,
            ],
        )
        self.assertTrue(progress_events[-1].done)
        self.assertIn("GIF", progress_events[-1].message)

    def test_wcs_align_then_crop_corrects_meridian_flip(self) -> None:
        from astropy.wcs import WCS

        from photometry_app.core.target_field_animation import crop_wcs_aligned_stamp

        reference = np.full((81, 81), 8.0)
        reference[39:42, 39:42] = 40.0
        reference[40, 40] = 90.0
        reference[18:21, 58:61] = 25.0
        reference[19, 59] = 70.0
        flipped = np.rot90(reference, 2)
        reference_wcs = WCS(naxis=2)
        reference_wcs.wcs.ctype = ["RA---TAN", "DEC--TAN"]
        reference_wcs.wcs.crval = [10.0, 20.0]
        reference_wcs.wcs.crpix = [41.0, 41.0]
        reference_wcs.wcs.cd = [[-0.002, 0.0], [0.0, 0.002]]
        flipped_wcs = reference_wcs.deepcopy()
        flipped_wcs.wcs.cd = [[0.002, 0.0], [0.0, -0.002]]

        aligned_crop = crop_wcs_aligned_stamp(
            flipped,
            flipped_wcs,
            reference_wcs,
            reference_shape=reference.shape,
            center_x=40.0,
            center_y=40.0,
            fov_px=25,
        )
        reference_crop = crop_target_stamp(reference, 40.0, 40.0, 25)

        self.assertIsNotNone(aligned_crop)
        assert aligned_crop is not None
        self.assertEqual(aligned_crop.shape, (25, 25))
        self.assertLessEqual(
            abs(int(np.unravel_index(int(np.nanargmax(aligned_crop)), aligned_crop.shape)[0]) - 12),
            2,
        )
        self.assertLess(
            float(np.nanmean(np.abs(aligned_crop[10:15, 10:15] - reference_crop[10:15, 10:15]))),
            8.0,
        )

    def test_normalize_stretch_mode_defaults_to_stf_bright(self) -> None:
        self.assertEqual(normalize_target_field_stretch_mode(None), DEFAULT_TARGET_FIELD_STRETCH_MODE)
        self.assertEqual(normalize_target_field_stretch_mode("asinh"), "asinh")
        self.assertEqual(normalize_target_field_stretch_mode("nope"), "stf_bright")

    def test_stf_bright_stretch_is_brighter_than_linear(self) -> None:
        rng = np.random.default_rng(1)
        sky = rng.normal(20.0, 1.6, (48, 48))
        sky[18:23, 18:23] += 25.0
        sky[20, 20] += 80.0

        linear = stretch_stamps_to_shared_display([sky], stretch_mode="linear")[0]
        bright = stretch_stamps_to_shared_display([sky], stretch_mode="stf_bright")[0]

        self.assertGreater(float(np.nanmedian(bright)), float(np.nanmedian(linear)))

    def test_crop_keeps_target_at_center(self) -> None:
        image = np.zeros((80, 100), dtype=float)
        image[30, 40] = 100.0

        stamp = crop_target_stamp(image, 40.0, 30.0, 21)

        self.assertEqual(stamp.shape, (21, 21))
        self.assertAlmostEqual(float(stamp[10, 10]), 100.0)

    def test_crop_pads_near_the_edge(self) -> None:
        image = np.ones((20, 20), dtype=float)

        stamp = crop_target_stamp(image, 1.0, 1.0, 10)

        self.assertEqual(stamp.shape, (10, 10))
        self.assertTrue(np.isnan(stamp[0, 0]))
        self.assertTrue(np.isfinite(stamp[-1, -1]))

    def test_background_match_equalizes_border_medians(self) -> None:
        dim = np.full((40, 40), 10.0)
        dim[18:23, 18:23] = 40.0
        bright = np.full((40, 40), 30.0)
        bright[18:23, 18:23] = 80.0

        matched = match_stamp_backgrounds([dim, bright])

        self.assertAlmostEqual(estimate_stamp_background(matched[0]), estimate_stamp_background(matched[1]), places=5)

    def test_background_estimate_rejects_stars_and_bright_crop_edges(self) -> None:
        stamp = np.full((50, 50), 12.0)
        stamp[0, :] = 80.0
        stamp[-1, :] = 80.0
        stamp[:, 0] = 80.0
        stamp[:, -1] = 80.0
        stamp[20:25, 20:25] = 150.0

        background = estimate_stamp_background(stamp)

        self.assertAlmostEqual(background, 12.0, places=5)

    def test_local_comparison_scales_use_only_references_inside_crop(self) -> None:
        target_measurements = [
            self._measurement(file_path=Path(f"frame_{index}.fits"), x=50.0, y=50.0, index=index)
            for index in range(3)
        ]
        local_fluxes = (100.0, 50.0, 200.0)
        distant_fluxes = (1000.0, 1000.0, 1000.0)
        local_references = [
            replace(
                target,
                source_id="local-reference",
                source_name="Local Reference",
                x=60.0,
                y=55.0,
                flux=flux,
                is_variable=False,
                is_reference=True,
            )
            for target, flux in zip(target_measurements, local_fluxes, strict=True)
        ]
        local_references[1] = replace(local_references[1], excluded_from_analysis=True)
        distant_references = [
            replace(
                target,
                source_id="distant-reference",
                source_name="Distant Reference",
                x=90.0,
                y=90.0,
                flux=flux,
                is_variable=False,
                is_reference=True,
            )
            for target, flux in zip(target_measurements, distant_fluxes, strict=True)
        ]
        report = ProcessingReport(
            object_name="Demo",
            files_processed=3,
            solved_files=3,
            field_catalog=FieldCatalog(center_ra_deg=10.0, center_dec_deg=20.0, radius_deg=1.0),
            measurements=[*target_measurements, *local_references, *distant_references],
            light_curves=[],
        )
        frames = [TargetFieldFrame(measurement=target, point=None) for target in target_measurements]

        scales = local_comparison_scale_factors(report, frames, fov_px=40)

        self.assertTrue(np.allclose(scales, [1.0, 2.0, 0.5]))

    def test_crop_comparison_scales_remove_common_cloud_dimming(self) -> None:
        rows, columns = np.indices((96, 96), dtype=float)
        throughputs = (1.0, 0.5, 1.25)
        backgrounds = (100.0, 125.0, 90.0)
        comparison_stars = ((20.0, 20.0, 80.0), (75.0, 24.0, 65.0), (22.0, 74.0, 55.0))
        target_signals = (140.0, 180.0, 100.0)
        stamps = []
        for index, (throughput, background) in enumerate(zip(throughputs, backgrounds, strict=True)):
            rng = np.random.default_rng(100 + index)
            stamp = background + rng.normal(0.0, 0.35, size=(96, 96))
            for x, y, signal in comparison_stars:
                stamp += throughput * signal * np.exp(-((columns - x) ** 2 + (rows - y) ** 2) / 4.5)
            stamp += (
                throughput
                * target_signals[index]
                * np.exp(-((columns - 47.5) ** 2 + (rows - 47.5) ** 2) / 4.5)
            )
            stamps.append(stamp)

        scales = crop_comparison_scale_factors(stamps)

        self.assertIsNotNone(scales)
        self.assertTrue(np.allclose(scales, [1.0, 2.0, 0.8], atol=0.04))

    def test_comparison_scaled_background_match_equalizes_stable_star_signal(self) -> None:
        stamps = []
        for background, star_signal in ((10.0, 20.0), (30.0, 10.0), (5.0, 40.0)):
            stamp = np.full((30, 30), background)
            stamp[14:17, 14:17] = background + star_signal
            stamps.append(stamp)

        matched = match_stamp_backgrounds(stamps, scale_factors=[1.0, 2.0, 0.5])

        backgrounds = [estimate_stamp_background(stamp) for stamp in matched]
        signals = [float(np.nanmax(stamp) - background) for stamp, background in zip(matched, backgrounds, strict=True)]
        self.assertTrue(np.allclose(backgrounds, [10.0, 10.0, 10.0]))
        self.assertTrue(np.allclose(signals, [20.0, 20.0, 20.0]))

    def test_shared_stretch_preserves_relative_star_brightness(self) -> None:
        faint = np.full((30, 30), 10.0)
        faint[14:17, 14:17] = 20.0
        bright = np.full((30, 30), 10.0)
        bright[14:17, 14:17] = 80.0
        matched = match_stamp_backgrounds([faint, bright])

        stretched = stretch_stamps_to_shared_display(matched)

        self.assertLess(float(np.nanmax(stretched[0])), float(np.nanmax(stretched[1])))

    def test_collect_frames_requires_a_target_and_matches_files(self) -> None:
        measurement = self._measurement(file_path=Path("frame_001.fits"), x=12.0, y=18.0, index=0)
        series = LightCurveSeries(
            object_name="Demo",
            source_id="vsx-demo",
            source_name="Demo Var",
            filter_name="V",
            points=[
                LightCurvePoint(
                    observation_time=measurement.observation_time,
                    file_path=measurement.file_path,
                    differential_magnitude=12.0,
                    instrumental_magnitude=10.0,
                    flux=1000.0,
                    flux_error=5.0,
                )
            ],
        )
        report = ProcessingReport(
            object_name="Demo",
            files_processed=1,
            solved_files=1,
            field_catalog=FieldCatalog(center_ra_deg=10.0, center_dec_deg=20.0, radius_deg=1.0),
            measurements=[measurement],
            light_curves=[series],
        )

        with self.assertRaises(TargetFieldAnimationError):
            collect_target_field_frames(report, "")
        frames = collect_target_field_frames(report, "vsx-demo", filter_name="V")
        self.assertEqual(len(frames), 1)
        self.assertIsNotNone(frames[0].point)

    def test_stamp_cache_round_trips(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            image_path = Path(temp_dir) / "frame.fits"
            data = np.zeros((40, 40), dtype=float)
            data[20, 20] = 55.0
            fits.PrimaryHDU(data=data).writeto(image_path)
            measurement = self._measurement(file_path=image_path, x=20.0, y=20.0, index=0)
            cache_dir = Path(temp_dir) / "cache"

            first = load_or_create_target_stamp(measurement, fov_px=11, cache_dir=cache_dir, source_id="vsx-demo")
            cached_files = list((cache_dir / "target-field-animation").glob("*.npy"))
            self.assertEqual(len(cached_files), 1)
            second = load_or_create_target_stamp(measurement, fov_px=11, cache_dir=cache_dir, source_id="vsx-demo")

            self.assertEqual(first.shape, (11, 11))
            self.assertTrue(np.allclose(first, second, equal_nan=True))
            self.assertAlmostEqual(float(first[5, 5]), 55.0)

    def test_parallel_workers_default_to_shared_auto_range(self) -> None:
        self.assertEqual(resolve_target_field_parallel_workers(1), 1)
        self.assertEqual(resolve_target_field_parallel_workers(12), 12)
        self.assertGreaterEqual(resolve_target_field_parallel_workers(None), 1)
        self.assertLessEqual(resolve_target_field_parallel_workers(0), 8)

    def test_threaded_crop_matches_single_worker(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            frames: list[TargetFieldFrame] = []
            for index in range(3):
                path = Path(temp_dir) / f"frame_{index}.fits"
                data = np.full((40, 40), 8.0 + index)
                data[20, 20] = 50.0 + index
                fits.PrimaryHDU(data).writeto(path)
                frames.append(TargetFieldFrame(measurement=self._measurement(file_path=path, x=20.0, y=20.0, index=index), point=None))
            kwargs = {
                "align_mode": TARGET_FIELD_ALIGN_NONE,
                "fov_px": 11,
                "cache_dir": None,
                "source_id": "vsx-demo",
                "reference_measurement": frames[0].measurement,
                "reference_image": None,
                "reference_crop": None,
                "positions_by_file": {},
                "progress_callback": None,
                "is_cancelled": None,
                "progress_message": lambda completed, total: f"{completed}/{total}",
            }
            sequential = _load_target_field_stamps_parallel(frames, max_workers=1, **kwargs)
            parallel = _load_target_field_stamps_parallel(frames, max_workers=3, **kwargs)
            self.assertEqual(len(sequential), 3)
            for left, right in zip(sequential, parallel, strict=True):
                self.assertTrue(np.allclose(left, right, equal_nan=True))

    def test_threaded_crop_then_align_matches_single_worker(self) -> None:
        reference = np.full((100, 100), 8.0)
        reference[49:52, 59:62] = 40.0
        reference[50, 60] = 90.0
        reference[29:32, 74:77] = 25.0
        reference[30, 75] = 70.0
        flipped = np.rot90(reference, 2)
        with tempfile.TemporaryDirectory() as temp_dir:
            reference_path = Path(temp_dir) / "reference.fits"
            flipped_path = Path(temp_dir) / "flipped.fits"
            extra_path = Path(temp_dir) / "extra.fits"
            fits.PrimaryHDU(reference).writeto(reference_path)
            fits.PrimaryHDU(flipped).writeto(flipped_path)
            fits.PrimaryHDU(reference).writeto(extra_path)
            frames = [
                TargetFieldFrame(measurement=self._measurement(file_path=reference_path, x=60.0, y=50.0, index=0), point=None),
                TargetFieldFrame(measurement=self._measurement(file_path=flipped_path, x=39.0, y=49.0, index=1), point=None),
                TargetFieldFrame(measurement=self._measurement(file_path=extra_path, x=60.0, y=50.0, index=2), point=None),
            ]
            kwargs = {
                "align_mode": TARGET_FIELD_ALIGN_CROP_THEN_ALIGN,
                "fov_px": 41,
                "cache_dir": None,
                "source_id": "vsx-demo",
                "reference_measurement": frames[0].measurement,
                "reference_image": reference,
                "reference_crop": crop_target_stamp(reference, 60.0, 50.0, 41),
                "positions_by_file": {},
                "progress_callback": None,
                "is_cancelled": None,
                "progress_message": lambda completed, total: f"{completed}/{total}",
            }
            sequential = _load_target_field_stamps_parallel(frames, max_workers=1, **kwargs)
            parallel = _load_target_field_stamps_parallel(frames, max_workers=3, **kwargs)
            self.assertEqual(len(sequential), 3)
            for left, right in zip(sequential, parallel, strict=True):
                self.assertTrue(np.allclose(left, right, equal_nan=True))

    def test_parallel_workers_leave_small_frames_uncapped(self) -> None:
        self.assertEqual(resolve_target_field_parallel_workers(8, frame_shape=(40, 40)), 8)
        large = resolve_target_field_parallel_workers(8, frame_shape=(4176, 6248))
        self.assertGreaterEqual(large, 1)
        self.assertLessEqual(large, 3)

    def test_prepare_does_not_keep_full_source_frames(self) -> None:
        reference = np.full((80, 90), 8.0)
        reference[29:32, 39:42] = 40.0
        reference[30, 40] = 90.0
        reference[14:17, 54:57] = 25.0
        reference[15, 55] = 70.0
        flipped = np.rot90(reference, 2)
        with tempfile.TemporaryDirectory() as temp_dir:
            reference_path = Path(temp_dir) / "reference.fits"
            flipped_path = Path(temp_dir) / "flipped.fits"
            fits.PrimaryHDU(reference).writeto(reference_path)
            fits.PrimaryHDU(flipped).writeto(flipped_path)
            reference_measurement = self._measurement(file_path=reference_path, x=40.0, y=30.0, index=0)
            flipped_measurement = self._measurement(file_path=flipped_path, x=49.0, y=49.0, index=1)
            kwargs = {
                "fov_px": 25,
                "cache_dir": None,
                "source_id": "vsx-demo",
                "reference_measurement": reference_measurement,
                "reference_image": reference,
                "reference_crop": crop_target_stamp(reference, 40.0, 30.0, 25),
                "reference_positions": None,
                "source_positions": None,
            }
            crop_then_align = _prepare_target_field_frame(
                1,
                flipped_measurement,
                align_mode=TARGET_FIELD_ALIGN_CROP_THEN_ALIGN,
                **kwargs,
            )
            align_then_crop = _prepare_target_field_frame(
                1,
                flipped_measurement,
                align_mode=TARGET_FIELD_ALIGN_ALIGN_THEN_CROP,
                **kwargs,
            )
        self.assertIsNone(getattr(crop_then_align, "source_image", None))
        self.assertIsNotNone(crop_then_align.stamp)
        self.assertIsNone(getattr(align_then_crop, "source_image", None))
        self.assertEqual(align_then_crop.image_path, flipped_path)
        self.assertIsNone(align_then_crop.stamp)

    def test_prepare_align_then_crop_uses_star_positions_without_reading_fits(self) -> None:
        reference = np.full((80, 90), 8.0)
        with tempfile.TemporaryDirectory() as temp_dir:
            reference_path = Path(temp_dir) / "reference.fits"
            source_path = Path(temp_dir) / "source.fits"
            fits.PrimaryHDU(reference).writeto(reference_path)
            fits.PrimaryHDU(reference).writeto(source_path)
            reference_measurement = self._measurement(file_path=reference_path, x=40.0, y=30.0, index=0)
            source_measurement = self._measurement(file_path=source_path, x=43.0, y=32.0, index=1)
            reference_positions = {"a": (40.0, 30.0), "b": (55.0, 15.0), "c": (22.0, 48.0)}
            source_positions = {"a": (43.0, 32.0), "b": (58.0, 17.0), "c": (25.0, 50.0)}
            with mock.patch(
                "photometry_app.core.target_field_animation.read_photometry_image_data",
            ) as reader:
                prepared = _prepare_target_field_frame(
                    1,
                    source_measurement,
                    align_mode=TARGET_FIELD_ALIGN_ALIGN_THEN_CROP,
                    fov_px=25,
                    cache_dir=None,
                    source_id="vsx-demo",
                    reference_measurement=reference_measurement,
                    reference_image=reference,
                    reference_crop=None,
                    reference_positions=reference_positions,
                    source_positions=source_positions,
                )
        reader.assert_not_called()
        self.assertIsNone(getattr(prepared, "source_image", None))
        self.assertIsNotNone(prepared.candidates)

    def test_threaded_align_then_crop_matches_single_worker(self) -> None:
        reference = np.full((100, 100), 8.0)
        reference[49:52, 59:62] = 40.0
        reference[50, 60] = 90.0
        reference[29:32, 74:77] = 25.0
        reference[30, 75] = 70.0
        flipped = np.rot90(reference, 2)
        with tempfile.TemporaryDirectory() as temp_dir:
            reference_path = Path(temp_dir) / "reference.fits"
            flipped_path = Path(temp_dir) / "flipped.fits"
            extra_path = Path(temp_dir) / "extra.fits"
            fits.PrimaryHDU(reference).writeto(reference_path)
            fits.PrimaryHDU(flipped).writeto(flipped_path)
            fits.PrimaryHDU(reference).writeto(extra_path)
            frames = [
                TargetFieldFrame(measurement=self._measurement(file_path=reference_path, x=60.0, y=50.0, index=0), point=None),
                TargetFieldFrame(measurement=self._measurement(file_path=flipped_path, x=39.0, y=49.0, index=1), point=None),
                TargetFieldFrame(measurement=self._measurement(file_path=extra_path, x=60.0, y=50.0, index=2), point=None),
            ]
            kwargs = {
                "align_mode": TARGET_FIELD_ALIGN_ALIGN_THEN_CROP,
                "fov_px": 41,
                "cache_dir": None,
                "source_id": "vsx-demo",
                "reference_measurement": frames[0].measurement,
                "reference_image": reference,
                "reference_crop": crop_target_stamp(reference, 60.0, 50.0, 41),
                "positions_by_file": {},
                "progress_callback": None,
                "is_cancelled": None,
                "progress_message": lambda completed, total: f"{completed}/{total}",
            }
            sequential = _load_target_field_stamps_parallel(frames, max_workers=1, **kwargs)
            parallel = _load_target_field_stamps_parallel(frames, max_workers=3, **kwargs)
            self.assertEqual(len(sequential), 3)
            for left, right in zip(sequential, parallel, strict=True):
                self.assertTrue(np.allclose(left, right, equal_nan=True))

    def test_threaded_align_then_crop_with_star_positions_matches_single_worker(self) -> None:
        reference = np.full((80, 90), 8.0)
        reference[29:32, 39:42] = 40.0
        reference[30, 40] = 90.0
        shifted = np.roll(np.roll(reference, 2, axis=0), -3, axis=1)
        with tempfile.TemporaryDirectory() as temp_dir:
            reference_path = Path(temp_dir) / "reference.fits"
            shifted_path = Path(temp_dir) / "shifted.fits"
            fits.PrimaryHDU(reference).writeto(reference_path)
            fits.PrimaryHDU(shifted).writeto(shifted_path)
            frames = [
                TargetFieldFrame(measurement=self._measurement(file_path=reference_path, x=40.0, y=30.0, index=0), point=None),
                TargetFieldFrame(measurement=self._measurement(file_path=shifted_path, x=37.0, y=32.0, index=1), point=None),
            ]
            positions_by_file = {
                str(reference_path): {"a": (40.0, 30.0), "b": (55.0, 15.0), "c": (22.0, 48.0)},
                str(shifted_path): {"a": (37.0, 32.0), "b": (52.0, 17.0), "c": (19.0, 50.0)},
            }
            kwargs = {
                "align_mode": TARGET_FIELD_ALIGN_ALIGN_THEN_CROP,
                "fov_px": 25,
                "cache_dir": None,
                "source_id": "vsx-demo",
                "reference_measurement": frames[0].measurement,
                "reference_image": reference,
                "reference_crop": None,
                "positions_by_file": positions_by_file,
                "progress_callback": None,
                "is_cancelled": None,
                "progress_message": lambda completed, total: f"{completed}/{total}",
            }
            sequential = _load_target_field_stamps_parallel(frames, max_workers=1, **kwargs)
            parallel = _load_target_field_stamps_parallel(frames, max_workers=3, **kwargs)
            self.assertEqual(len(sequential), 2)
            for left, right in zip(sequential, parallel, strict=True):
                self.assertTrue(np.allclose(left, right, equal_nan=True))

    def test_pointer_marker_leaves_center_open_and_omits_right_bottom_arms(self) -> None:
        from PySide6.QtGui import QColor, QImage
        from PySide6.QtWidgets import QApplication

        QApplication.instance() or QApplication([])
        image = QImage(80, 80, QImage.Format.Format_RGB888)
        image.fill(QColor("#000000"))
        marked = apply_target_field_marker(
            image,
            style="pointer",
            appearance=TargetMarkerAppearance(
                line_color="#ff0000",
                outline_color="",
                line_width=2.0,
                length_percent=90.0,
            ),
        )
        center = marked.pixelColor(40, 40)
        self.assertEqual((center.red(), center.green(), center.blue()), (0, 0, 0))
        outer, gap = target_field_marker_extents(80, 80, 90)
        left = marked.pixelColor(int(round(40 - outer + 2)), 40)
        self.assertGreater(left.red(), 80)
        top = marked.pixelColor(40, int(round(40 - outer + 2)))
        self.assertGreater(top.red(), 80)
        right = marked.pixelColor(76, 40)
        self.assertEqual((right.red(), right.green(), right.blue()), (0, 0, 0))
        bottom = marked.pixelColor(40, 76)
        self.assertEqual((bottom.red(), bottom.green(), bottom.blue()), (0, 0, 0))

    def test_shorter_marker_length_keeps_arms_away_from_stamp_edges(self) -> None:
        from PySide6.QtGui import QColor, QImage
        from PySide6.QtWidgets import QApplication

        QApplication.instance() or QApplication([])
        image = QImage(80, 80, QImage.Format.Format_RGB888)
        image.fill(QColor("#000000"))
        marked = apply_target_field_marker(
            image,
            style="pointer",
            appearance=TargetMarkerAppearance(
                line_color="#ff0000",
                outline_color="",
                line_width=2.0,
                length_percent=30.0,
            ),
        )
        self.assertEqual(marked.pixelColor(4, 40).red(), 0)
        self.assertEqual(marked.pixelColor(40, 4).red(), 0)
        outer, gap = target_field_marker_extents(80, 80, 30)
        arm_x = int(round(40.0 - ((outer + gap) / 2.0)))
        self.assertGreater(marked.pixelColor(arm_x, 40).red(), 80)

    def test_preview_render_includes_pointer_on_synthetic_stamp(self) -> None:
        from PySide6.QtWidgets import QApplication

        QApplication.instance() or QApplication([])
        preview = render_target_field_marker_preview(
            None,
            None,
            None,
            fov_px=80,
            stretch_mode="linear",
            marker_style="pointer",
            appearance=TargetMarkerAppearance(line_color="#ff0000", outline_color="", line_width=2.0, length_percent=80.0),
        )
        self.assertFalse(preview.isNull())
        self.assertEqual(preview.width(), 80)
        self.assertGreater(preview.pixelColor(int(round(40 - target_field_marker_extents(80, 80, 80)[0] + 2)), 40).red(), 40)
