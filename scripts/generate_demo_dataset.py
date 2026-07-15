from __future__ import annotations

import math
from datetime import datetime, timedelta
from pathlib import Path

import numpy as np
from astropy.io import fits
from astropy.wcs import WCS

from photometry_app.core.catalogs import CatalogService
from photometry_app.core.matching import select_reference_stars
from photometry_app.core.models import SolvedField
from photometry_app.core.settings import AppSettings


def main() -> int:
    workspace_root = Path(__file__).resolve().parents[1]
    settings = AppSettings.from_root(workspace_root)
    object_name = "DemoOrion"
    output_dir = workspace_root / "Files" / object_name
    output_dir.mkdir(parents=True, exist_ok=True)

    width = 768
    height = 768
    pixel_scale_deg = 0.0015
    wcs = _build_wcs(width, height, center_ra_deg=83.822, center_dec_deg=-5.391, pixel_scale_deg=pixel_scale_deg)
    solved_field = SolvedField(
        center_ra_deg=83.822,
        center_dec_deg=-5.391,
        radius_deg=0.85,
        width=width,
        height=height,
        wcs_path=output_dir / "template.fits",
    )

    catalog_service = CatalogService(settings.cache_dir / "catalogs")
    catalog = catalog_service.query_field_catalog(solved_field)
    variable_stars = [star for star in catalog.variable_stars if _inside_image(wcs, star.ra_deg, star.dec_deg, width, height)]
    if not variable_stars:
        raise RuntimeError("No cataloged VSX variable stars were found inside the demo field.")

    reference_stars = [
        star for star in select_reference_stars(catalog.gaia_stars, variable_stars, limit=8)
        if _inside_image(wcs, star.ra_deg, star.dec_deg, width, height)
    ]
    if len(reference_stars) < 3:
        fallback_stars = [
            star
            for star in catalog.gaia_stars
            if _inside_image(wcs, star.ra_deg, star.dec_deg, width, height)
            and star.magnitude is not None
            and 8.0 <= star.magnitude <= 16.0
            and all(abs(star.ra_deg - variable.ra_deg) > 0.002 or abs(star.dec_deg - variable.dec_deg) > 0.002 for variable in variable_stars)
        ]
        fallback_stars.sort(key=lambda star: star.magnitude if star.magnitude is not None else 99.0)
        reference_stars = fallback_stars[:8]
    if len(reference_stars) < 3:
        raise RuntimeError("Not enough Gaia reference stars were available inside the demo field.")

    variable_star = variable_stars[0]
    selected_references = reference_stars[:3]
    start_time = datetime(2026, 3, 16, 1, 0, 0)

    for frame_index in range(5):
        image = np.random.normal(loc=900.0, scale=8.0, size=(height, width)).astype(np.float32)
        phase = frame_index / 4.0
        variable_flux = 18000.0 + 8000.0 * math.sin(phase * math.pi * 1.5)
        _draw_star(image, wcs, variable_star.ra_deg, variable_star.dec_deg, variable_flux)

        for star_index, reference_star in enumerate(selected_references):
            _draw_star(image, wcs, reference_star.ra_deg, reference_star.dec_deg, 22000.0 + 1200.0 * star_index)

        header = wcs.to_header()
        header["DATE-OBS"] = (start_time + timedelta(minutes=30 * frame_index)).isoformat()
        header["FILTER"] = "R"
        header["EXPTIME"] = 60.0
        header["OBJECT"] = object_name
        header["BUNIT"] = "ADU"

        file_path = output_dir / f"demo_{frame_index + 1:02d}.fits"
        fits.PrimaryHDU(data=image, header=header).writeto(file_path, overwrite=True)

    print(f"Created demo dataset in {output_dir}")
    print(f"Variable star: {variable_star.name} ({variable_star.ra_deg:.5f}, {variable_star.dec_deg:.5f})")
    print("Reference stars:")
    for star in selected_references:
        print(f"- {star.name} ({star.ra_deg:.5f}, {star.dec_deg:.5f})")
    return 0


def _build_wcs(width: int, height: int, center_ra_deg: float, center_dec_deg: float, pixel_scale_deg: float) -> WCS:
    wcs = WCS(naxis=2)
    wcs.wcs.crpix = [width / 2, height / 2]
    wcs.wcs.crval = [center_ra_deg, center_dec_deg]
    wcs.wcs.ctype = ["RA---TAN", "DEC--TAN"]
    wcs.wcs.cd = np.array([[-pixel_scale_deg, 0.0], [0.0, pixel_scale_deg]])
    return wcs


def _inside_image(wcs: WCS, ra_deg: float, dec_deg: float, width: int, height: int) -> bool:
    x, y = wcs.world_to_pixel_values(ra_deg, dec_deg)
    return 12 <= x < (width - 12) and 12 <= y < (height - 12)


def _draw_star(image: np.ndarray, wcs: WCS, ra_deg: float, dec_deg: float, amplitude: float) -> None:
    x_center, y_center = wcs.world_to_pixel_values(ra_deg, dec_deg)
    sigma = 2.0
    x_min = max(0, int(x_center) - 8)
    x_max = min(image.shape[1], int(x_center) + 9)
    y_min = max(0, int(y_center) - 8)
    y_max = min(image.shape[0], int(y_center) + 9)

    y_indices, x_indices = np.mgrid[y_min:y_max, x_min:x_max]
    gaussian = amplitude * np.exp(-(((x_indices - x_center) ** 2) + ((y_indices - y_center) ** 2)) / (2 * sigma ** 2))
    image[y_min:y_max, x_min:x_max] += gaussian.astype(np.float32)


if __name__ == "__main__":
    raise SystemExit(main())