from __future__ import annotations

import unittest
from pathlib import Path

from astropy.io.fits import Header

from photometry_app.core.pointing import assess_image_pointing, mount_pointing_coordinate


class ImagePointingAssessmentTest(unittest.TestCase):
    def test_mount_pointing_ignores_crval_keywords(self) -> None:
        header = Header()
        header["CRVAL1"] = 10.0
        header["CRVAL2"] = 20.0
        header["RA"] = 329.492404325075
        header["DEC"] = 48.9374364745146

        mount = mount_pointing_coordinate(header)

        self.assertIsNotNone(mount)
        assert mount is not None
        self.assertAlmostEqual(mount.ra.deg, 329.492404325075, places=6)
        self.assertAlmostEqual(mount.dec.deg, 48.9374364745146, places=6)

    def test_assessment_prefers_mount_when_wcs_is_only_ctype_stub(self) -> None:
        header = Header()
        header["CTYPE1"] = "RA---TAN"
        header["CTYPE2"] = "DEC--TAN"
        header["EQUINOX"] = 2000
        header["RA"] = 329.492404325075
        header["DEC"] = 48.9374364745146
        header["FOCALLEN"] = 530.0
        header["XPIXSZ"] = 3.76
        header["YPIXSZ"] = 3.76

        assessment = assess_image_pointing(header, 6252, 4176, source_path=Path("stub.xisf"))

        self.assertEqual(assessment.agreement, "mount_only")
        self.assertEqual(assessment.preferred_source, "mount")
        self.assertAlmostEqual(assessment.preferred_ra_deg or 0.0, 329.492404325075, places=6)
        self.assertFalse(assessment.wcs_usable)
        self.assertTrue(any("no usable celestial WCS" in message.lower() or "not usable" in message.lower() for message in assessment.messages))
        self.assertTrue(any("Mount/header pointing" in message for message in assessment.messages))

    def test_assessment_flags_disagreement_when_wcs_and_mount_differ(self) -> None:
        header = Header()
        header["CTYPE1"] = "RA---TAN"
        header["CTYPE2"] = "DEC--TAN"
        header["CRVAL1"] = 80.0
        header["CRVAL2"] = 10.0
        header["CRPIX1"] = 500.0
        header["CRPIX2"] = 400.0
        header["CD1_1"] = -0.0004
        header["CD1_2"] = 0.0
        header["CD2_1"] = 0.0
        header["CD2_2"] = 0.0004
        header["RA"] = 329.492404325075
        header["DEC"] = 48.9374364745146
        header["FOCALLEN"] = 530.0
        header["XPIXSZ"] = 3.76

        assessment = assess_image_pointing(header, 1000, 800)

        self.assertEqual(assessment.agreement, "disagree")
        self.assertTrue(assessment.prefer_astrometry_first)
        self.assertIsNotNone(assessment.separation_deg)
        assert assessment.separation_deg is not None
        self.assertGreater(assessment.separation_deg, 1.0)
        self.assertTrue(any("disagree" in message.lower() for message in assessment.messages))


if __name__ == "__main__":
    unittest.main()
