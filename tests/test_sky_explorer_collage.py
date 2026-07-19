from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

import numpy as np
from astropy.io import fits
from astropy.wcs import WCS

from photometry_app.core.plotting import AnnotatedImageRenderSettings
from photometry_app.core.sky_explorer import SkyExplorerObject
from photometry_app.core.sky_explorer_collage import (
    SkyExplorerCollageCrop,
    SkyExplorerCollageOptions,
    automatic_collage_column_count,
    build_sky_explorer_collage,
    compose_sky_explorer_collage,
    crop_sky_explorer_object_from_rendered,
    order_sky_explorer_mosaic_crops,
    partition_sky_explorer_collage_objects,
    save_sky_explorer_collage_image,
    sky_explorer_catalog_size_axes,
    sky_explorer_object_crop_pixel_bounds,
    sky_explorer_object_has_catalog_size,
)


def _make_wcs(
    *,
    width: int = 200,
    height: int = 160,
    cd1_1: float = -1.0 / 3600.0,
    cd1_2: float = 0.0,
    cd2_1: float = 0.0,
    cd2_2: float = 1.0 / 3600.0,
) -> WCS:
    header = fits.Header()
    header["NAXIS"] = 2
    header["NAXIS1"] = width
    header["NAXIS2"] = height
    header["CTYPE1"] = "RA---TAN"
    header["CTYPE2"] = "DEC--TAN"
    header["CRVAL1"] = 180.0
    header["CRVAL2"] = 30.0
    header["CRPIX1"] = width / 2.0 + 0.5
    header["CRPIX2"] = height / 2.0 + 0.5
    header["CD1_1"] = cd1_1
    header["CD1_2"] = cd1_2
    header["CD2_1"] = cd2_1
    header["CD2_2"] = cd2_2
    return WCS(header, naxis=2)


def _sky_object(
    *,
    name: str = "M 31",
    pixel_x: float = 100.0,
    pixel_y: float = 80.0,
    ra_deg: float | None = None,
    dec_deg: float | None = None,
    metadata: dict[str, object] | None = None,
    wcs: WCS | None = None,
) -> SkyExplorerObject:
    if ra_deg is None or dec_deg is None:
        active_wcs = wcs or _make_wcs()
        world = active_wcs.pixel_to_world(pixel_x, pixel_y)
        ra_deg = float(world.ra.deg)
        dec_deg = float(world.dec.deg)
    return SkyExplorerObject(
        "deep_sky",
        "simbad",
        name,
        name,
        "Galaxy",
        float(ra_deg),
        float(dec_deg),
        float(pixel_x),
        float(pixel_y),
        8.0,
        1.0,
        name,
        metadata or {},
    )


class SkyExplorerCollageCoreTest(unittest.TestCase):
    def test_eligibility_requires_positive_catalog_size(self) -> None:
        with_size = _sky_object(metadata={"catalog_size_arcmin": 3.0})
        with_axes = _sky_object(
            name="Ellipse",
            metadata={
                "catalog_major_axis_arcmin": 4.0,
                "catalog_minor_axis_arcmin": 2.0,
                "catalog_position_angle_deg": 0.0,
            },
        )
        without_size = _sky_object(name="Star", metadata={"catalog_type": "Star"})
        zero_size = _sky_object(name="Zero", metadata={"catalog_size_arcmin": 0.0})

        self.assertTrue(sky_explorer_object_has_catalog_size(with_size))
        self.assertTrue(sky_explorer_object_has_catalog_size(with_axes))
        self.assertFalse(sky_explorer_object_has_catalog_size(without_size))
        self.assertFalse(sky_explorer_object_has_catalog_size(zero_size))

        axes = sky_explorer_catalog_size_axes(with_axes)
        assert axes is not None
        self.assertEqual(axes.position_angle_deg, 0.0)

        eligible, skipped = partition_sky_explorer_collage_objects((with_size, without_size, with_axes, zero_size))
        self.assertEqual([obj.name for obj in eligible], ["M 31", "Ellipse"])
        self.assertEqual([obj.name for obj in skipped], ["Star", "Zero"])

    def test_default_margin_expands_crop_by_fifteen_percent(self) -> None:
        wcs = _make_wcs()
        sky_object = _sky_object(
            wcs=wcs,
            metadata={"catalog_size_arcmin": 2.0},
        )
        bounds_no_margin = sky_explorer_object_crop_pixel_bounds(
            sky_object,
            wcs,
            margin_fraction=0.0,
            image_width=200,
            image_height=160,
        )
        bounds_margin = sky_explorer_object_crop_pixel_bounds(
            sky_object,
            wcs,
            margin_fraction=0.15,
            image_width=200,
            image_height=160,
        )
        assert bounds_no_margin is not None
        assert bounds_margin is not None
        width_no_margin = bounds_no_margin[2] - bounds_no_margin[0]
        height_no_margin = bounds_no_margin[3] - bounds_no_margin[1]
        width_margin = bounds_margin[2] - bounds_margin[0]
        height_margin = bounds_margin[3] - bounds_margin[1]
        self.assertGreater(width_margin, width_no_margin)
        self.assertGreater(height_margin, height_no_margin)
        self.assertAlmostEqual(width_margin / width_no_margin, 1.15, delta=0.08)
        self.assertAlmostEqual(height_margin / height_no_margin, 1.15, delta=0.08)

    def test_rotated_and_anisotropic_wcs_crops_remain_centered(self) -> None:
        wcs = _make_wcs(
            width=400,
            height=320,
            cd1_1=-0.8 / 3600.0,
            cd1_2=-0.4 / 3600.0,
            cd2_1=-0.3 / 3600.0,
            cd2_2=1.1 / 3600.0,
        )
        sky_object = _sky_object(
            wcs=wcs,
            pixel_x=200.0,
            pixel_y=160.0,
            metadata={
                "catalog_major_axis_arcmin": 1.5,
                "catalog_minor_axis_arcmin": 0.6,
                "catalog_position_angle_deg": 35.0,
            },
        )
        rendered = np.zeros((320, 400, 3), dtype=np.uint8)
        rendered[:, :] = (10, 20, 30)
        crop = crop_sky_explorer_object_from_rendered(
            rendered,
            sky_object,
            wcs,
            margin_fraction=0.15,
        )
        assert crop is not None
        bounds = sky_explorer_object_crop_pixel_bounds(
            sky_object,
            wcs,
            margin_fraction=0.15,
            image_width=400,
            image_height=320,
        )
        assert bounds is not None
        x0, y0, x1, y1, _was_clipped, was_padded = bounds
        self.assertFalse(was_padded)
        self.assertFalse(crop.was_padded)
        self.assertGreater(crop.requested_width, 10)
        self.assertGreater(crop.requested_height, 10)
        # Axis-aligned crop is centered on the object's pixel coordinates.
        self.assertAlmostEqual((x0 + x1) / 2.0, float(sky_object.pixel_x), delta=1.5)
        self.assertAlmostEqual((y0 + y1) / 2.0, float(sky_object.pixel_y), delta=1.5)
        # Anisotropic/rotated WCS should not produce a square bounding box in general.
        self.assertNotEqual(crop.requested_width, crop.requested_height)

    def test_edge_crop_is_padded_and_keeps_requested_size(self) -> None:
        wcs = _make_wcs()
        sky_object = _sky_object(
            wcs=wcs,
            pixel_x=8.0,
            pixel_y=8.0,
            metadata={"catalog_size_arcmin": 4.0},
        )
        rendered = np.full((160, 200, 3), 40, dtype=np.uint8)
        crop = crop_sky_explorer_object_from_rendered(
            rendered,
            sky_object,
            wcs,
            margin_fraction=0.15,
            pad_rgb=(1, 2, 3),
        )
        assert crop is not None
        self.assertTrue(crop.was_padded)
        self.assertTrue(crop.was_clipped)
        self.assertEqual(crop.image_rgb.shape[1], crop.requested_width)
        self.assertEqual(crop.image_rgb.shape[0], crop.requested_height)
        self.assertTrue(np.any(np.all(crop.image_rgb == (1, 2, 3), axis=2)))

    def test_uniform_and_natural_layouts_and_labels(self) -> None:
        crops = (
            SkyExplorerCollageCrop(
                sky_object=_sky_object(name="Small", metadata={"catalog_size_arcmin": 1.0}),
                image_rgb=np.full((40, 50, 3), 80, dtype=np.uint8),
                was_clipped=False,
                was_padded=False,
                requested_width=50,
                requested_height=40,
            ),
            SkyExplorerCollageCrop(
                sky_object=_sky_object(name="Large", metadata={"catalog_size_arcmin": 2.0}),
                image_rgb=np.full((90, 120, 3), 120, dtype=np.uint8),
                was_clipped=False,
                was_padded=False,
                requested_width=120,
                requested_height=90,
            ),
        )
        uniform, uniform_columns, uniform_rows = compose_sky_explorer_collage(
            crops,
            SkyExplorerCollageOptions(layout="uniform", show_labels=True, columns=2, gap_px=8, padding_px=10),
        )
        natural, natural_columns, natural_rows = compose_sky_explorer_collage(
            crops,
            SkyExplorerCollageOptions(layout="natural", show_labels=False, columns=2, gap_px=8, padding_px=10),
        )
        self.assertEqual((uniform_columns, uniform_rows), (2, 1))
        self.assertEqual((natural_columns, natural_rows), (2, 1))
        self.assertEqual(uniform.shape[0], 10 + 90 + 28 + 10)
        self.assertEqual(uniform.shape[1], 10 + 120 + 8 + 120 + 10)
        self.assertEqual(natural.shape[0], 10 + 90 + 10)
        self.assertEqual(natural.shape[1], 10 + 50 + 8 + 120 + 10)
        self.assertEqual(automatic_collage_column_count(5), 3)

    def test_mosaic_packs_interlocking_tiles_without_letterboxing(self) -> None:
        crop_specs = (
            ("Small", 1.0, 50, 40, 70),
            ("Largest", 4.0, 120, 80, 110),
            ("Tiny", 0.5, 30, 20, 150),
            ("Medium", 2.0, 80, 60, 190),
            ("Wide", 1.5, 100, 40, 220),
            ("Tall", 1.8, 40, 90, 40),
        )
        crops = tuple(
            SkyExplorerCollageCrop(
                sky_object=_sky_object(name=name, metadata={"catalog_size_arcmin": size}),
                image_rgb=np.full((height, width, 3), color, dtype=np.uint8),
                was_clipped=False,
                was_padded=False,
                requested_width=width,
                requested_height=height,
            )
            for name, size, width, height, color in crop_specs
        )

        ordered = order_sky_explorer_mosaic_crops(crops)
        self.assertEqual(ordered[0].sky_object.name, "Largest")
        self.assertEqual(
            [crop.sky_object.name for crop in ordered],
            ["Largest", "Medium", "Tall", "Wide", "Small", "Tiny"],
        )

        mosaic, columns, rows = compose_sky_explorer_collage(
            crops,
            SkyExplorerCollageOptions(
                layout="mosaic",
                show_labels=False,
                gap_px=16,
                padding_px=12,
                mosaic_target_width_px=640,
                canvas_rgb=(1, 2, 3),
            ),
        )
        self.assertGreaterEqual(columns, 1)
        self.assertGreaterEqual(rows, 1)
        self.assertEqual(mosaic.shape[1], 640)
        self.assertGreater(mosaic.shape[0], 300)
        # Themed gutters remain visible, while solid black letterboxing is absent.
        pixel_colors = {tuple(int(value) for value in color) for color in mosaic.reshape(-1, 3)}
        self.assertIn((1, 2, 3), pixel_colors)
        # Cover-fit tiles should include original crop colors somewhere in the mosaic.
        self.assertTrue(
            {(110, 110, 110), (190, 190, 190), (70, 70, 70)} & pixel_colors
        )
        # Interior of the content area should mostly be filled by tiles/gutters, not empty bands.
        content = mosaic[12:-12, 12:-12]
        background_fraction = float(np.mean(np.all(content == (1, 2, 3), axis=2)))
        self.assertLess(background_fraction, 0.22)

    def test_build_collage_end_to_end_and_save(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            image_path = root / "field.fits"
            wcs = _make_wcs(width=120, height=100)
            data = np.linspace(0.0, 1.0, 120 * 100, dtype=np.float32).reshape(100, 120)
            header = wcs.to_header()
            fits.PrimaryHDU(data=data, header=header).writeto(image_path)

            objects = (
                _sky_object(
                    name="Galaxy A",
                    wcs=wcs,
                    pixel_x=40.0,
                    pixel_y=50.0,
                    metadata={"catalog_size_arcmin": 1.5},
                ),
                _sky_object(
                    name="Galaxy B",
                    wcs=wcs,
                    pixel_x=80.0,
                    pixel_y=55.0,
                    metadata={
                        "catalog_major_axis_arcmin": 2.0,
                        "catalog_minor_axis_arcmin": 1.0,
                        "catalog_position_angle_deg": 20.0,
                    },
                ),
                _sky_object(name="No Size", wcs=wcs, pixel_x=60.0, pixel_y=40.0, metadata={}),
            )
            result = build_sky_explorer_collage(
                source_image_path=image_path,
                objects=objects,
                wcs=wcs,
                render_settings=AnnotatedImageRenderSettings(stretch_mode="linear"),
                options=SkyExplorerCollageOptions(layout="uniform", margin_fraction=0.15, show_labels=True),
            )
            self.assertEqual(len(result.included_objects), 2)
            self.assertEqual(len(result.skipped_objects), 1)
            self.assertEqual(result.layout, "uniform")
            self.assertAlmostEqual(result.margin_fraction, 0.15)
            self.assertGreater(result.image_rgb.shape[0], 0)
            self.assertGreater(result.image_rgb.shape[1], 0)

            output_path = root / "collage.png"
            save_sky_explorer_collage_image(result.image_rgb, output_path)
            self.assertTrue(output_path.exists())
            self.assertGreater(output_path.stat().st_size, 0)


if __name__ == "__main__":
    unittest.main()
