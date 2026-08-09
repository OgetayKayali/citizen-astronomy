from __future__ import annotations

import tempfile
import unittest
from datetime import datetime
from pathlib import Path

import numpy as np
from astropy.io import fits
from astropy.wcs import WCS

from photometry_app.core.models import (
    FileScanResult,
    ManualPhotometryConfig,
    ManualSourceConfig,
    ManualSourceRole,
    ObjectPhotometryMode,
    ObservationMetadata,
    PlateSolveResult,
    SolvedField,
    WcsStatus,
)
from photometry_app.core.pipeline import _refresh_manual_config_sky_coordinates


def _tan_wcs(width: int = 200, height: int = 200, crval=(180.0, 10.0), scale_arcsec: float = 1.0) -> WCS:
    scale = scale_arcsec / 3600.0
    header = fits.Header(
        {
            "NAXIS": 2,
            "NAXIS1": width,
            "NAXIS2": height,
            "CTYPE1": "RA---TAN",
            "CTYPE2": "DEC--TAN",
            "CRPIX1": width / 2.0,
            "CRPIX2": height / 2.0,
            "CRVAL1": crval[0],
            "CRVAL2": crval[1],
            "CD1_1": -scale,
            "CD1_2": 0.0,
            "CD2_1": 0.0,
            "CD2_2": scale,
            "CUNIT1": "deg",
            "CUNIT2": "deg",
        }
    )
    return WCS(header)


class ManualSkyRefreshTest(unittest.TestCase):
    def test_refresh_manual_config_sky_coordinates_from_reference_pixels(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            true_wcs = _tan_wcs(crval=(180.0, 10.0), scale_arcsec=1.0)
            # Saved RA/Dec were derived from a WCS whose CRVAL is ~87" off.
            bad_wcs = _tan_wcs(crval=(180.0242, 10.0), scale_arcsec=1.0)
            x_pix, y_pix = 120.0, 90.0
            true_ra, true_dec = true_wcs.pixel_to_world_values(x_pix, y_pix)
            bad_ra, bad_dec = bad_wcs.pixel_to_world_values(x_pix, y_pix)

            wcs_path = root / "repaired_wcs.fits"
            header = true_wcs.to_header(relax=True)
            header["NAXIS"] = 2
            header["NAXIS1"] = 200
            header["NAXIS2"] = 200
            header["CRVAL1"] = 180.0
            header["CRVAL2"] = 10.0
            fits.PrimaryHDU(data=np.zeros((200, 200), dtype=np.float32), header=header).writeto(
                wcs_path, overwrite=True
            )
            loaded = WCS(fits.getheader(wcs_path))
            loaded_ra, loaded_dec = loaded.pixel_to_world_values(x_pix, y_pix)
            self.assertAlmostEqual(float(loaded_ra), float(true_ra), places=5)
            self.assertAlmostEqual(float(loaded_dec), float(true_dec), places=5)

            solved = SolvedField(
                center_ra_deg=180.0,
                center_dec_deg=10.0,
                radius_deg=0.1,
                width=200,
                height=200,
                wcs_path=wcs_path,
            )
            file_result = FileScanResult(
                path=root / "frame.fits",
                object_folder="Demo",
                metadata=ObservationMetadata(
                    date_obs=datetime(2026, 1, 1),
                    filter_name="V",
                    exposure_seconds=60.0,
                    width=200,
                    height=200,
                    object_name="Demo",
                ),
                wcs_status=WcsStatus.SOLVED,
            )
            plate = PlateSolveResult(
                source_path=file_result.path,
                status=WcsStatus.SOLVED,
                solved_field=solved,
                reasons=[],
            )
            config = ManualPhotometryConfig(
                object_name="Demo",
                mode=ObjectPhotometryMode.MANUAL,
                reference_frame_name="frame.fits",
                sources=[
                    ManualSourceConfig(
                        source_id="manual-comp-1",
                        name="Comp 1",
                        role=ManualSourceRole.COMPARISON,
                        ra_deg=float(bad_ra),
                        dec_deg=float(bad_dec),
                        reference_frame_name="frame.fits",
                        reference_x=x_pix,
                        reference_y=y_pix,
                        aperture_radius=5.0,
                        annulus_inner_radius=8.0,
                        annulus_outer_radius=12.0,
                    )
                ],
            )

            updated, notes = _refresh_manual_config_sky_coordinates(config, [(file_result, plate)], solved)

            self.assertTrue(notes)
            source = updated.sources[0]
            self.assertAlmostEqual(source.ra_deg, float(true_ra), places=5)
            self.assertAlmostEqual(source.dec_deg, float(true_dec), places=5)
            self.assertGreater(abs(float(bad_ra) - source.ra_deg) * 3600.0, 50.0)


if __name__ == "__main__":
    unittest.main()
