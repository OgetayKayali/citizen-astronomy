from __future__ import annotations

from collections import OrderedDict
from concurrent.futures import Future
import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock

import numpy as np
from PIL import Image
from PySide6.QtGui import QColor, QImage

from photometry_app.ui.sky_view_milky_way_gl import (
    OpenGLMilkyWayLayer,
    SkyViewMilkyWayTileDrawRequest,
    SkyViewMilkyWayTileMeshVertex,
    _SkyViewMilkyWayBaseUploadFutureResult,
    _SkyViewMilkyWayPreparedTileCacheEntry,
    _SkyViewMilkyWayPreparedTileFutureResult,
    _SkyViewMilkyWayTextureCacheEntry,
)


class SkyViewMilkyWayGlHelperTest(unittest.TestCase):
    def test_tile_draw_request_debug_defaults_are_safe(self) -> None:
        tile_request = SkyViewMilkyWayTileDrawRequest(
            texture_path=Path("tile.png"),
            texture_cache_key=("tile.png", 1),
            level=4,
            x_index=31,
            y_index=7,
            vertices=(SkyViewMilkyWayTileMeshVertex(0.0, 0.0, 0.0, 0.0),),
        )

        self.assertFalse(tile_request.is_missing)
        self.assertIsNone(tile_request.has_signal)
        self.assertEqual(tile_request.tile_renderer, "shader")
        self.assertEqual(tile_request.uv_bounds, (0.0, 1.0, 0.0, 1.0))
        self.assertFalse(tile_request.ra_wrap)
        self.assertEqual(tile_request.tile_grid_shape, (1, 1))
        self.assertEqual(tile_request.subdivision_columns, 0)
        self.assertEqual(tile_request.subdivision_rows, 0)
        self.assertEqual(tile_request.triangle_count, 0)
        self.assertEqual(tile_request.skipped_triangle_count, 0)
        self.assertEqual(tile_request.max_triangle_area, 0.0)

    def test_upload_image_adds_mask_alpha_to_legacy_rgb_texture(self) -> None:
        image = QImage(32, 16, QImage.Format.Format_RGB888)
        image.fill(QColor(0, 0, 0))
        for y_index in range(16):
            for x_index in range(16, 32):
                image.setPixelColor(x_index, y_index, QColor(220, 220, 220))

        upload_image = OpenGLMilkyWayLayer._milky_way_upload_image(image)

        self.assertTrue(upload_image.hasAlphaChannel())
        self.assertEqual(upload_image.pixelColor(0, 8).alpha(), 0)
        self.assertGreater(upload_image.pixelColor(31, 8).alpha(), 200)

    def test_decoded_tile_image_preserves_existing_alpha(self) -> None:
        layer = OpenGLMilkyWayLayer()

        with tempfile.TemporaryDirectory() as temp_dir:
            tile_path = Path(temp_dir) / "tile.png"
            tile_image = QImage(4, 4, QImage.Format.Format_RGBA8888)
            tile_image.fill(QColor(10, 20, 30, 0))
            tile_image.setPixelColor(2, 2, QColor(100, 120, 140, 180))
            self.assertTrue(tile_image.save(str(tile_path)))

            decoded_image, _source_format = layer._resolve_decoded_tile_image(tile_path, cache_budget_bytes=1024 * 1024)

            self.assertFalse(decoded_image.isNull())
            self.assertTrue(decoded_image.hasAlphaChannel())
            self.assertEqual(decoded_image.pixelColor(0, 0).alpha(), 0)
            self.assertEqual(decoded_image.pixelColor(2, 2).alpha(), 180)

    def test_shader_tile_sampling_uses_half_open_bounds_without_overlap(self) -> None:
        left_tile = SkyViewMilkyWayTileDrawRequest(
            texture_path=Path("left.png"),
            texture_cache_key=("left.png", 1),
            level=4,
            x_index=7,
            y_index=2,
            vertices=(),
            uv_bounds=(0.25, 0.5, 0.375, 0.5),
            dec_bounds_deg=(0.0, 22.5),
        )
        right_tile = SkyViewMilkyWayTileDrawRequest(
            texture_path=Path("right.png"),
            texture_cache_key=("right.png", 1),
            level=4,
            x_index=8,
            y_index=2,
            vertices=(),
            uv_bounds=(0.5, 0.75, 0.375, 0.5),
            dec_bounds_deg=(0.0, 22.5),
        )

        self.assertFalse(OpenGLMilkyWayLayer.shader_tile_contains_sample(left_tile, global_u=0.5, dec_deg=12.0))
        self.assertTrue(OpenGLMilkyWayLayer.shader_tile_contains_sample(right_tile, global_u=0.5, dec_deg=12.0))

    def test_shader_tile_local_uv_handles_ra_seam_samples(self) -> None:
        seam_tile = SkyViewMilkyWayTileDrawRequest(
            texture_path=Path("seam.png"),
            texture_cache_key=("seam.png", 1),
            level=4,
            x_index=0,
            y_index=0,
            vertices=(),
            uv_bounds=(0.0, 0.03125, 0.0, 0.0625),
            dec_bounds_deg=(78.75, 90.0),
            ra_wrap=True,
        )

        local_uv = OpenGLMilkyWayLayer.shader_tile_local_uv(seam_tile, global_u=0.015625, dec_deg=84.375)

        self.assertIsNotNone(local_uv)
        assert local_uv is not None
        self.assertAlmostEqual(local_uv[0], 0.5, places=4)
        self.assertAlmostEqual(local_uv[1], 0.5, places=4)
        self.assertIsNone(OpenGLMilkyWayLayer.shader_tile_local_uv(seam_tile, global_u=0.03125, dec_deg=84.375))

    def test_tile_cache_eviction_is_lru(self) -> None:
        layer = OpenGLMilkyWayLayer()

        first_texture = MagicMock()
        second_texture = MagicMock()
        third_texture = MagicMock()

        layer._tile_textures = OrderedDict(
            (
                (("first",), _SkyViewMilkyWayTextureCacheEntry(first_texture, 4, 4, 2, 2, 1, False, 24)),
                (("second",), _SkyViewMilkyWayTextureCacheEntry(second_texture, 4, 4, 2, 2, 1, False, 24)),
                (("third",), _SkyViewMilkyWayTextureCacheEntry(third_texture, 4, 4, 2, 2, 1, False, 24)),
            )
        )
        layer._tile_cache_total_bytes = 72

        layer._evict_tile_textures_to_budget(48)

        self.assertEqual(list(layer._tile_textures.keys()), [("second",), ("third",)])
        first_texture.release.assert_called_once_with()
        first_texture.destroy.assert_called_once_with()
        second_texture.release.assert_not_called()
        third_texture.release.assert_not_called()
        self.assertEqual(layer._tile_cache_total_bytes, 48)

    def test_tile_cache_eviction_preserves_current_frame_textures(self) -> None:
        layer = OpenGLMilkyWayLayer()

        old_texture = MagicMock()
        current_first_texture = MagicMock()
        current_second_texture = MagicMock()

        layer._tile_textures = OrderedDict(
            (
                (("old",), _SkyViewMilkyWayTextureCacheEntry(old_texture, 4, 4, 2, 2, 1, False, 24)),
                (("current-first",), _SkyViewMilkyWayTextureCacheEntry(current_first_texture, 4, 4, 2, 2, 1, False, 24)),
                (("current-second",), _SkyViewMilkyWayTextureCacheEntry(current_second_texture, 4, 4, 2, 2, 1, False, 24)),
            )
        )
        layer._tile_cache_total_bytes = 72

        layer._evict_tile_textures_to_budget(
            48,
            protected_cache_keys={("current-first",), ("current-second",)},
        )

        self.assertEqual(list(layer._tile_textures.keys()), [("current-first",), ("current-second",)])
        old_texture.release.assert_called_once_with()
        old_texture.destroy.assert_called_once_with()
        current_first_texture.release.assert_not_called()
        current_second_texture.release.assert_not_called()
        self.assertEqual(layer._tile_cache_total_bytes, 48)

    def test_prepared_tile_cache_eviction_is_lru(self) -> None:
        layer = OpenGLMilkyWayLayer()

        image = QImage(4, 4, QImage.Format.Format_RGB888)
        image.fill(0)

        layer._prepared_tiles = OrderedDict(
            (
                (("first",), _SkyViewMilkyWayPreparedTileCacheEntry(image, 4, 4, 2, 2, 1, "13", "13", 24, (), 0.1, 0.01, 0.02, 0.03, 0.04)),
                (("second",), _SkyViewMilkyWayPreparedTileCacheEntry(image, 4, 4, 2, 2, 1, "13", "13", 24, (), 0.1, 0.01, 0.02, 0.03, 0.04)),
                (("third",), _SkyViewMilkyWayPreparedTileCacheEntry(image, 4, 4, 2, 2, 1, "13", "13", 24, (), 0.1, 0.01, 0.02, 0.03, 0.04)),
            )
        )
        layer._prepared_tile_cache_total_bytes = 72

        layer._evict_prepared_tiles_to_budget(48)

        self.assertEqual(list(layer._prepared_tiles.keys()), [("second",), ("third",)])
        self.assertEqual(layer._prepared_tile_cache_total_bytes, 48)

    def test_resolve_tile_texture_respects_upload_limit(self) -> None:
        layer = OpenGLMilkyWayLayer()

        with tempfile.TemporaryDirectory() as temp_dir:
            tile_path = Path(temp_dir) / "tile.png"
            tile_image = QImage(8, 8, QImage.Format.Format_RGB888)
            tile_image.fill(0)
            self.assertTrue(tile_image.save(str(tile_path)))

            tile_request = SkyViewMilkyWayTileDrawRequest(
                texture_path=tile_path,
                texture_cache_key=(str(tile_path), 1, 1, 0, 0, 0),
                level=0,
                x_index=0,
                y_index=0,
                vertices=(SkyViewMilkyWayTileMeshVertex(0.0, 0.0, 0.0, 0.0),),
            )

            entry, cache_hit, uploaded = layer._resolve_tile_texture(
                tile_request,
                remaining_uploads=0,
                cache_budget_bytes=1024,
                prepared_cache_budget_bytes=1024,
            )

            self.assertIsNone(entry)
            self.assertFalse(cache_hit)
            self.assertFalse(uploaded)
            self.assertEqual(layer._tile_cache_total_bytes, 0)

    def test_resolve_tile_texture_schedules_prepare_without_blocking_decode(self) -> None:
        layer = OpenGLMilkyWayLayer()

        with tempfile.TemporaryDirectory() as temp_dir:
            tile_path = Path(temp_dir) / "tile.png"
            tile_image = QImage(8, 8, QImage.Format.Format_RGB888)
            tile_image.fill(0)
            self.assertTrue(tile_image.save(str(tile_path)))

            tile_request = SkyViewMilkyWayTileDrawRequest(
                texture_path=tile_path,
                texture_cache_key=("async", str(tile_path)),
                level=0,
                x_index=0,
                y_index=0,
                vertices=(SkyViewMilkyWayTileMeshVertex(0.0, 0.0, 0.0, 0.0),),
            )

            scheduled: list[SkyViewMilkyWayTileDrawRequest] = []
            layer._resolve_prepared_tile = MagicMock(side_effect=AssertionError("resolve must not prepare synchronously"))
            layer._create_texture = MagicMock(side_effect=AssertionError("upload requires a prepared tile"))
            layer._schedule_prepared_tile_future = MagicMock(side_effect=lambda request: scheduled.append(request) or True)

            entry, cache_hit, uploaded = layer._resolve_tile_texture(
                tile_request,
                remaining_uploads=1,
                cache_budget_bytes=1024,
                prepared_cache_budget_bytes=1024,
            )

            self.assertIsNone(entry)
            self.assertFalse(cache_hit)
            self.assertFalse(uploaded)
            self.assertEqual(scheduled, [tile_request])
            self.assertEqual(layer.last_tile_cpu_cache_misses, 1)
            layer._resolve_prepared_tile.assert_not_called()
            layer._create_texture.assert_not_called()

    def test_prepared_tile_future_harvest_respects_per_frame_cap(self) -> None:
        layer = OpenGLMilkyWayLayer()
        image = QImage(4, 4, QImage.Format.Format_RGBA8888)
        image.fill(0)

        for index in range(3):
            cache_key = ("future", index)
            entry = _SkyViewMilkyWayPreparedTileCacheEntry(
                image,
                4,
                4,
                2,
                2,
                1,
                "source",
                "upload",
                64,
                (),
                0.1,
                0.01,
                0.02,
                0.03,
                0.04,
            )
            result = _SkyViewMilkyWayPreparedTileFutureResult(
                cache_key=cache_key,
                entry=entry,
                decode_success_count=1,
                decode_failure_count=0,
                decode_failure_path="none",
                decode_failure_reason="none",
                file_read_seconds=0.01,
                decode_seconds=0.02,
                convert_seconds=0.03,
                padding_seconds=0.04,
                prepare_total_seconds=0.1,
            )
            future: Future[_SkyViewMilkyWayPreparedTileFutureResult] = Future()
            future.set_result(result)
            layer._prepared_tile_futures[cache_key] = future

        harvested = layer._harvest_prepared_tile_futures(cache_budget_bytes=1024 * 1024, max_completed=2)

        self.assertEqual(harvested, 2)
        self.assertEqual(len(layer._prepared_tiles), 2)
        self.assertEqual(len(layer._prepared_tile_futures), 1)
        self.assertEqual(layer.last_prepared_tile_future_harvested, 2)
        self.assertEqual(layer.last_prepared_tile_future_pending, 1)
        self.assertAlmostEqual(layer.last_prepared_tile_future_prepare_total_seconds, 0.2)

    def test_ensure_texture_schedules_base_prepare_without_alpha_mask_in_draw(self) -> None:
        layer = OpenGLMilkyWayLayer()
        source_image = QImage(16, 8, QImage.Format.Format_RGB888)
        source_image.fill(0)

        layer._create_texture = MagicMock(side_effect=AssertionError("base upload must wait for prepared image"))
        layer._schedule_base_upload_prepare = MagicMock()

        layer._ensure_texture(source_image, ("base", 1))

        layer._schedule_base_upload_prepare.assert_called_once_with(source_image, ("base", 1))
        layer._create_texture.assert_not_called()

    def test_ensure_texture_uploads_only_finished_base_prepare(self) -> None:
        layer = OpenGLMilkyWayLayer()
        source_image = QImage(16, 8, QImage.Format.Format_RGB888)
        source_image.fill(0)
        upload_image = QImage(16, 8, QImage.Format.Format_RGBA8888)
        upload_image.fill(0)
        texture = MagicMock()

        future: Future[_SkyViewMilkyWayBaseUploadFutureResult] = Future()
        future.set_result(
            _SkyViewMilkyWayBaseUploadFutureResult(
                cache_key=("base", 1),
                upload_image=upload_image,
                prepare_seconds=0.25,
            )
        )
        layer._base_upload_future = future
        layer._base_upload_future_key = ("base", 1)
        layer._create_texture = MagicMock(return_value=(texture, 16, 8, True))

        layer._ensure_texture(source_image, ("base", 1))

        layer._create_texture.assert_called_once_with(upload_image, repeat_s=True, upload_ready=True)
        self.assertIs(layer._texture, texture)
        self.assertEqual(layer._texture_cache_key, ("base", 1))
        self.assertTrue(layer.last_base_upload_future_harvested)
        self.assertAlmostEqual(layer.last_base_upload_prepare_seconds, 0.25)

    def test_neighbor_tile_path_wraps_columns_and_clamps_rows(self) -> None:
        tile_request = SkyViewMilkyWayTileDrawRequest(
            texture_path=Path("root") / "L4" / "0_0.png",
            texture_cache_key=("tile",),
            level=4,
            x_index=0,
            y_index=0,
            vertices=(),
            tile_grid_shape=(32, 16),
        )

        self.assertEqual(OpenGLMilkyWayLayer._neighbor_tile_path(tile_request, dx=-1, dy=0), Path("root") / "L4" / "31_0.png")
        self.assertEqual(OpenGLMilkyWayLayer._neighbor_tile_path(tile_request, dx=1, dy=0), Path("root") / "L4" / "1_0.png")
        self.assertIsNone(OpenGLMilkyWayLayer._neighbor_tile_path(tile_request, dx=0, dy=-1))

    def test_tile_image_border_uses_neighbor_edges(self) -> None:
        layer = OpenGLMilkyWayLayer()

        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir) / "L0"
            root.mkdir(parents=True)

            for x_index, value in ((0, 10), (1, 200)):
                tile_image = QImage(2, 2, QImage.Format.Format_RGB888)
                tile_image.fill(QColor(value, value, value))
                self.assertTrue(tile_image.save(str(root / f"{x_index}_0.png")))

            tile_request = SkyViewMilkyWayTileDrawRequest(
                texture_path=root / "0_0.png",
                texture_cache_key=("tile",),
                level=0,
                x_index=0,
                y_index=0,
                vertices=(),
                tile_grid_shape=(2, 1),
            )

            padded_image = layer._load_tile_image_with_neighbor_border(tile_request)

            self.assertFalse(padded_image.isNull())
            self.assertEqual((padded_image.width(), padded_image.height()), (4, 4))
            self.assertEqual(padded_image.pixelColor(0, 1).red(), 200)
            self.assertEqual(padded_image.pixelColor(3, 1).red(), 200)
            self.assertEqual(padded_image.pixelColor(1, 1).red(), 10)

    def test_read_image_file_decodes_tiff_lzw_bytes(self) -> None:
        layer = OpenGLMilkyWayLayer()

        with tempfile.TemporaryDirectory() as temp_dir:
            tile_path = Path(temp_dir) / "tile.tiff"
            pixels = np.arange(4 * 5 * 3, dtype=np.uint8).reshape((4, 5, 3))
            Image.fromarray(pixels, mode="RGB").save(tile_path, compression="tiff_lzw")

            image, file_read_seconds, decode_seconds = layer._read_image_file(tile_path)

            self.assertFalse(image.isNull())
            self.assertGreaterEqual(file_read_seconds, 0.0)
            self.assertGreaterEqual(decode_seconds, 0.0)
            self.assertEqual((image.width(), image.height()), (5, 4))

    def test_decode_failure_tracks_path_and_failure_count(self) -> None:
        layer = OpenGLMilkyWayLayer()

        with tempfile.TemporaryDirectory() as temp_dir:
            tile_path = Path(temp_dir) / "broken.tiff"
            tile_path.write_bytes(b"not-a-real-tiff")

            image, source_format = layer._resolve_decoded_tile_image(tile_path, cache_budget_bytes=1024 * 1024)

            self.assertTrue(image.isNull())
            self.assertEqual(source_format, "unknown")
            self.assertEqual(layer.last_tile_decode_success_count, 0)
            self.assertEqual(layer.last_tile_decode_failure_count, 1)
            self.assertEqual(layer.last_tile_decode_failure_path, str(tile_path))
            self.assertEqual(
                layer.last_tile_decode_failure_reason,
                "TIFF tile decode failed; Qt TIFF image plugin unavailable or decode error.",
            )

    def test_padded_tiles_skip_runtime_neighbor_padding(self) -> None:
        layer = OpenGLMilkyWayLayer()

        with tempfile.TemporaryDirectory() as temp_dir:
            tile_path = Path(temp_dir) / "tile.png"
            tile_image = QImage(4, 4, QImage.Format.Format_RGB888)
            tile_image.fill(QColor(10, 20, 30))
            tile_image.setPixelColor(0, 1, QColor(90, 90, 90))
            tile_image.setPixelColor(3, 1, QColor(120, 120, 120))
            self.assertTrue(tile_image.save(str(tile_path)))

            tile_request = SkyViewMilkyWayTileDrawRequest(
                texture_path=tile_path,
                texture_cache_key=("padded", str(tile_path), 1),
                level=0,
                x_index=0,
                y_index=0,
                vertices=(),
                padded_tile=True,
                gutter_pixels=1,
                content_region=(1, 1, 2, 2),
                tile_grid_shape=(1, 1),
            )

            prepared_entry, cache_hit = layer._resolve_prepared_tile(tile_request, cache_budget_bytes=1024 * 1024)

            self.assertFalse(cache_hit)
            self.assertIsNotNone(prepared_entry)
            assert prepared_entry is not None
            self.assertEqual(prepared_entry.neighbor_tile_ids, ())
            self.assertEqual(prepared_entry.width, 4)
            self.assertEqual(prepared_entry.height, 4)
            self.assertEqual(prepared_entry.core_width, 2)
            self.assertEqual(prepared_entry.core_height, 2)
            self.assertEqual(prepared_entry.padding_seconds, 0.0)

    def test_runtime_padding_reuses_decoded_neighbor_tiles_across_adjacent_prepares(self) -> None:
        layer = OpenGLMilkyWayLayer()

        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir) / "L0"
            root.mkdir(parents=True)

            for x_index, value in ((0, 10), (1, 200)):
                tile_image = QImage(2, 2, QImage.Format.Format_RGB888)
                tile_image.fill(QColor(value, value, value))
                self.assertTrue(tile_image.save(str(root / f"{x_index}_0.png")))

            first_request = SkyViewMilkyWayTileDrawRequest(
                texture_path=root / "0_0.png",
                texture_cache_key=("tile", 0),
                level=0,
                x_index=0,
                y_index=0,
                vertices=(),
                tile_grid_shape=(2, 1),
            )
            second_request = SkyViewMilkyWayTileDrawRequest(
                texture_path=root / "1_0.png",
                texture_cache_key=("tile", 1),
                level=0,
                x_index=1,
                y_index=0,
                vertices=(),
                tile_grid_shape=(2, 1),
            )

            original_read_image_file = layer._read_image_file
            read_counts: dict[str, int] = {}

            def counting_read(image_path: Path):
                read_counts[str(image_path)] = read_counts.get(str(image_path), 0) + 1
                return original_read_image_file(image_path)

            layer._read_image_file = counting_read

            try:
                layer._resolve_prepared_tile(first_request, cache_budget_bytes=1024 * 1024)
                layer._resolve_prepared_tile(second_request, cache_budget_bytes=1024 * 1024)
            finally:
                layer._read_image_file = original_read_image_file

            self.assertEqual(read_counts[str(root / "0_0.png")], 1)
            self.assertEqual(read_counts[str(root / "1_0.png")], 1)

    def test_prepare_tile_cache_hit_avoids_reloading_and_padding(self) -> None:
        layer = OpenGLMilkyWayLayer()

        with tempfile.TemporaryDirectory() as temp_dir:
            tile_path = Path(temp_dir) / "tile.png"
            tile_image = QImage(8, 8, QImage.Format.Format_RGB888)
            tile_image.fill(0)
            self.assertTrue(tile_image.save(str(tile_path)))

            tile_request = SkyViewMilkyWayTileDrawRequest(
                texture_path=tile_path,
                texture_cache_key=("manifest-a", str(tile_path), 1, 0, 0, "neighbor_border_1"),
                level=0,
                x_index=0,
                y_index=0,
                vertices=(),
            )

            first_entry, first_hit = layer._resolve_prepared_tile(tile_request, cache_budget_bytes=1024 * 1024)

            self.assertIsNotNone(first_entry)
            self.assertFalse(first_hit)
            self.assertEqual(layer.last_tile_cpu_cache_misses, 1)

            initial_read = layer.last_tile_file_read_seconds
            initial_decode = layer.last_tile_decode_seconds
            initial_padding = layer.last_tile_padding_seconds

            layer.last_tile_file_read_seconds = 0.0
            layer.last_tile_decode_seconds = 0.0
            layer.last_tile_convert_seconds = 0.0
            layer.last_tile_padding_seconds = 0.0
            layer.last_tile_prepare_total_seconds = 0.0

            second_entry, second_hit = layer._resolve_prepared_tile(tile_request, cache_budget_bytes=1024 * 1024)

            self.assertIs(second_entry, first_entry)
            self.assertTrue(second_hit)
            self.assertEqual(layer.last_tile_cpu_cache_hits, 1)
            self.assertEqual(layer.last_tile_file_read_seconds, 0.0)
            self.assertEqual(layer.last_tile_decode_seconds, 0.0)
            self.assertEqual(layer.last_tile_padding_seconds, 0.0)
            self.assertEqual(layer.last_tile_prepare_total_seconds, 0.0)
            self.assertGreater(initial_read, 0.0)
            self.assertGreater(initial_decode, 0.0)
            self.assertGreater(initial_padding, 0.0)

    def test_warm_prepared_tile_cache_schedules_only_missing_entries(self) -> None:
        layer = OpenGLMilkyWayLayer()

        with tempfile.TemporaryDirectory() as temp_dir:
            tile_paths = (Path(temp_dir) / "tile_0.png", Path(temp_dir) / "tile_1.png")

            for tile_path in tile_paths:
                tile_image = QImage(4, 4, QImage.Format.Format_RGB888)
                tile_image.fill(QColor(20, 30, 40))
                self.assertTrue(tile_image.save(str(tile_path)))

            requests = tuple(
                SkyViewMilkyWayTileDrawRequest(
                    texture_path=tile_path,
                    texture_cache_key=("warm", str(tile_path)),
                    level=0,
                    x_index=index,
                    y_index=0,
                    vertices=(),
                    padded_tile=True,
                    gutter_pixels=1,
                    content_region=(1, 1, 2, 2),
                    tile_grid_shape=(2, 1),
                )
                for index, tile_path in enumerate(tile_paths)
            )

            layer._resolve_prepared_tile = MagicMock(side_effect=AssertionError("warmup must not block"))
            scheduled_keys: list[tuple[object, ...]] = []

            def scheduling_probe(tile_request: SkyViewMilkyWayTileDrawRequest) -> bool:
                cache_key = tuple(tile_request.texture_cache_key)
                if cache_key in layer._prepared_tiles or cache_key in layer._prepared_tile_futures:
                    return False
                layer._prepared_tile_futures[cache_key] = Future()
                scheduled_keys.append(cache_key)
                return True

            layer._schedule_prepared_tile_future = scheduling_probe

            try:
                first_scheduled = layer.warm_prepared_tile_cache(requests, cache_budget_bytes=4 * 1024 * 1024, max_new_tiles=1)
                second_scheduled = layer.warm_prepared_tile_cache(requests, cache_budget_bytes=4 * 1024 * 1024)
                third_scheduled = layer.warm_prepared_tile_cache(requests, cache_budget_bytes=4 * 1024 * 1024)
            finally:
                layer._prepared_tile_futures.clear()

            self.assertEqual(first_scheduled, 1)
            self.assertEqual(second_scheduled, 1)
            self.assertEqual(third_scheduled, 0)
            self.assertEqual(set(scheduled_keys), {tuple(request.texture_cache_key) for request in requests})
            layer._resolve_prepared_tile.assert_not_called()

    def test_prepared_tile_cache_key_includes_padding_and_manifest_identity(self) -> None:
        layer = OpenGLMilkyWayLayer()

        with tempfile.TemporaryDirectory() as temp_dir:
            tile_path = Path(temp_dir) / "tile.png"
            tile_image = QImage(8, 8, QImage.Format.Format_RGB888)
            tile_image.fill(0)
            self.assertTrue(tile_image.save(str(tile_path)))

            first_request = SkyViewMilkyWayTileDrawRequest(
                texture_path=tile_path,
                texture_cache_key=("manifest-a", str(tile_path), 1, 0, 0, "neighbor_border_1"),
                level=0,
                x_index=0,
                y_index=0,
                vertices=(),
            )
            second_request = SkyViewMilkyWayTileDrawRequest(
                texture_path=tile_path,
                texture_cache_key=("manifest-b", str(tile_path), 1, 0, 0, "neighbor_border_1"),
                level=0,
                x_index=0,
                y_index=0,
                vertices=(),
            )
            third_request = SkyViewMilkyWayTileDrawRequest(
                texture_path=tile_path,
                texture_cache_key=("manifest-a", str(tile_path), 1, 0, 0, "neighbor_border_2"),
                level=0,
                x_index=0,
                y_index=0,
                vertices=(),
            )

            layer._resolve_prepared_tile(first_request, cache_budget_bytes=4 * 1024 * 1024)
            layer._resolve_prepared_tile(second_request, cache_budget_bytes=4 * 1024 * 1024)
            layer._resolve_prepared_tile(third_request, cache_budget_bytes=4 * 1024 * 1024)

            self.assertEqual(len(layer._prepared_tiles), 3)

    def test_gpu_cache_miss_uses_prepared_tile_cache_for_reupload(self) -> None:
        layer = OpenGLMilkyWayLayer()

        texture = MagicMock()
        texture.isCreated.return_value = True

        with tempfile.TemporaryDirectory() as temp_dir:
            tile_path = Path(temp_dir) / "tile.png"
            tile_image = QImage(8, 8, QImage.Format.Format_RGB888)
            tile_image.fill(0)
            self.assertTrue(tile_image.save(str(tile_path)))

            tile_request = SkyViewMilkyWayTileDrawRequest(
                texture_path=tile_path,
                texture_cache_key=("manifest-a", str(tile_path), 1, 0, 0, "neighbor_border_1"),
                level=0,
                x_index=0,
                y_index=0,
                vertices=(),
            )

            prepared_entry, _prepared_hit = layer._resolve_prepared_tile(tile_request, cache_budget_bytes=4 * 1024 * 1024)
            self.assertIsNotNone(prepared_entry)

            layer._tile_textures.clear()
            layer._tile_cache_total_bytes = 0
            layer.last_tile_cpu_cache_hits = 0
            layer.last_tile_cpu_cache_misses = 0
            layer.last_tile_file_read_seconds = 0.0
            layer.last_tile_decode_seconds = 0.0
            layer.last_tile_convert_seconds = 0.0
            layer.last_tile_padding_seconds = 0.0
            layer.last_tile_prepare_total_seconds = 0.0

            layer._create_texture = MagicMock(return_value=(texture, 10, 10, True))

            entry, cache_hit, uploaded = layer._resolve_tile_texture(
                tile_request,
                remaining_uploads=1,
                cache_budget_bytes=4 * 1024 * 1024,
                prepared_cache_budget_bytes=4 * 1024 * 1024,
            )

            self.assertIsNotNone(entry)
            self.assertFalse(cache_hit)
            self.assertTrue(uploaded)
            self.assertEqual(layer.last_tile_cpu_cache_hits, 1)
            self.assertEqual(layer.last_tile_cpu_cache_misses, 0)
            self.assertEqual(layer.last_tile_file_read_seconds, 0.0)
            self.assertEqual(layer.last_tile_decode_seconds, 0.0)
            self.assertEqual(layer.last_tile_padding_seconds, 0.0)
            self.assertEqual(layer.last_tile_prepare_total_seconds, 0.0)
            layer._create_texture.assert_called_once()

    def test_exact_debug_output_modes_require_opaque_writes(self) -> None:
        self.assertTrue(OpenGLMilkyWayLayer._debug_output_requires_exact_write("tile_id_color", debug_uv_enabled=False))
        self.assertTrue(OpenGLMilkyWayLayer._debug_output_requires_exact_write("tile_solid_id_color", debug_uv_enabled=False))
        self.assertTrue(OpenGLMilkyWayLayer._debug_output_requires_exact_write("raw_tile_rgb", debug_uv_enabled=False))
        self.assertTrue(OpenGLMilkyWayLayer._debug_output_requires_exact_write("tile_raw_opaque", debug_uv_enabled=False))
        self.assertTrue(OpenGLMilkyWayLayer._debug_output_requires_exact_write("owner_mask", debug_uv_enabled=False))
        self.assertTrue(OpenGLMilkyWayLayer._debug_output_requires_exact_write("tile_coverage_mask", debug_uv_enabled=False))
        self.assertTrue(OpenGLMilkyWayLayer._debug_output_requires_exact_write("local_uv_packed", debug_uv_enabled=False))
        self.assertTrue(OpenGLMilkyWayLayer._debug_output_requires_exact_write("global_uv_packed", debug_uv_enabled=False))
        self.assertTrue(OpenGLMilkyWayLayer._debug_output_requires_exact_write("tile_id_exact", debug_uv_enabled=False))
        self.assertTrue(OpenGLMilkyWayLayer._debug_output_requires_exact_write("final_preblend_packed_lo", debug_uv_enabled=False))
        self.assertFalse(OpenGLMilkyWayLayer._debug_output_requires_exact_write("final", debug_uv_enabled=False))
        self.assertFalse(OpenGLMilkyWayLayer._debug_output_requires_exact_write("tile_raw_normal_alpha", debug_uv_enabled=False))
        self.assertFalse(OpenGLMilkyWayLayer._debug_output_requires_exact_write("local_uv_packed", debug_uv_enabled=True))

    def test_tile_layer_debug_mode_aliases_map_to_expected_shader_branches(self) -> None:
        self.assertEqual(
            OpenGLMilkyWayLayer._debug_output_mode_value("tile_solid_id_color", debug_uv_enabled=False),
            OpenGLMilkyWayLayer._debug_output_mode_value("tile_id_color", debug_uv_enabled=False),
        )
        self.assertEqual(
            OpenGLMilkyWayLayer._debug_output_mode_value("tile_coverage_mask", debug_uv_enabled=False),
            OpenGLMilkyWayLayer._debug_output_mode_value("owner_mask", debug_uv_enabled=False),
        )
        self.assertEqual(
            OpenGLMilkyWayLayer._debug_output_mode_value("tile_raw_opaque", debug_uv_enabled=False),
            OpenGLMilkyWayLayer._debug_output_mode_value("raw_tile_rgb", debug_uv_enabled=False),
        )
        self.assertEqual(OpenGLMilkyWayLayer._debug_output_mode_value("tile_alpha_mask", debug_uv_enabled=False), 7)
        self.assertEqual(OpenGLMilkyWayLayer._debug_output_mode_value("tile_raw_normal_alpha", debug_uv_enabled=False), 5)


if __name__ == "__main__":
    unittest.main()