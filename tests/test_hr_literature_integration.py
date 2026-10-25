from __future__ import annotations

import csv
import json
import os
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from photometry_app.core.hr_diagram import HrMeasurementRow, HrWorkingTable, hr_working_table_payload
from photometry_app.ui.main_window import MainWindow


def measured_star():
    return HrMeasurementRow(
        source_id="3029206452090564480", source_name="HD 61954", catalog="gaia-dr3",
        ra_deg=115.284749, dec_deg=-14.85763, gaia_g_mag=9.416, gaia_bp_rp=0.060519,
        parallax_mas=0.7338, parallax_error_mas=0.02, x=10, y=20,
        aperture_radius=5, annulus_inner_radius=8, annulus_outer_radius=12,
    )


class HrLiteratureIntegrationTest(unittest.TestCase):
    def test_generation_enriches_rows_before_serialization_and_exports_provenance(self):
        star = measured_star()
        table = HrWorkingTable(rows=[star], plane_labels=[], measured_count=1, usable_count=1)
        field = SimpleNamespace(center_ra_deg=115.4, center_dec_deg=-14.8, radius_deg=0.8, wcs_path=None)
        records = [{"main_id": "HD 61954", "ra": star.ra_deg, "dec": star.dec_deg,
                    "ids": "Gaia DR3 " + star.source_id, "sp_type": "B6Ib",
                    "sp_bibcode": "1988MSS...C04....0H", "sp_qual": "C"}]
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            host = SimpleNamespace(
                _root_path_input=SimpleNamespace(text=lambda: directory),
                _settings=SimpleNamespace(hr_max_sources=10000),
                _hr_max_sources_spin=SimpleNamespace(value=lambda: 10000),
                _hr_catalog_cache_dir=lambda: root,
            )
            with patch("photometry_app.ui.main_window.measure_hr_sources", return_value=table), patch("photometry_app.core.hr_spectral_types._query_field", return_value=records):
                result = MainWindow._build_hr_working_table(host, root / "M46.xisf", SimpleNamespace(gaia_stars=[]), solved_field=field)
            self.assertTrue(result.spectral_lookup_complete)
            saved = json.loads(json.dumps(hr_working_table_payload(result)))
            restored = HrMeasurementRow(**saved["rows"][0])
            self.assertEqual(restored.literature_spectral_type, "B6Ib")
            self.assertEqual(restored.spectral_type_reference, "1988MSS...C04....0H")
            destination = root / "hr.csv"
            MainWindow._write_hr_rows_csv(host, destination, result.rows)
            with destination.open(newline="", encoding="utf-8") as handle:
                exported = next(csv.DictReader(handle))
            self.assertEqual(exported["spectral_type_match_method"], "Gaia DR3 identifier")
            self.assertEqual(exported["spectral_type_source"], "CDS/SIMBAD")
            self.assertEqual(exported["spectral_type_quality"], "C")

    def test_popup_shows_bulk_literature_provenance_even_while_details_load(self):
        star = measured_star()
        star.literature_spectral_type = "B6Ib"
        star.spectral_type_source = "CDS/SIMBAD"
        star.spectral_type_reference = "1988MSS...C04....0H"
        star.spectral_type_match_method = "Gaia DR3 identifier"
        host = SimpleNamespace(_preferred_hr_row_display_name=lambda row, **kwargs: row.source_name)
        text = MainWindow._format_hr_plot_popup_text(host, star, details=None, loading=True)
        self.assertIn("B6Ib (literature; CDS/SIMBAD)", text)
        self.assertIn("1988MSS...C04....0H", text)
        self.assertIn("color estimate", text)
        self.assertNotIn("loading", text)

    def test_failed_lookup_does_not_get_stuck_in_measurement_reuse(self):
        table = HrWorkingTable(rows=[], plane_labels=[], measured_count=0, usable_count=0, spectral_lookup_complete=False)
        host = SimpleNamespace(_current_hr_working_table=table, _current_hr_source_image=Path("M46.xisf"))
        self.assertFalse(MainWindow._can_reuse_cached_hr_measurements(host, Path("M46.xisf")))
