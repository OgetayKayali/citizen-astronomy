from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

import requests

from photometry_app.core.hr_spectral_types import (
    _query_field,
    apply_literature_types,
    enrich_hr_spectral_types,
    literature_luminosity_class,
    literature_spectral_type,
)


def row(source_id="1", **kwargs):
    return SimpleNamespace(**dict(
        dict(source_id=source_id, catalog="gaia-dr3", ra_deg=115.0, dec_deg=-15.0,
             pm_ra_mas_per_year=0.0, pm_dec_mas_per_year=0.0, literature_spectral_type=None), **kwargs))


def record(**kwargs):
    return dict(dict(main_id="HD 61954", ra=115.0, dec=-15.0, sp_type="B6Ib",
                     sp_bibcode="1988MSS...C04....0H", sp_qual="C", ids="HD 61954|Gaia DR3 1"), **kwargs)


class SpectralTypesTest(unittest.TestCase):
    def test_parses_supported_types_without_inventing_ambiguous_classes(self):
        for text, spectral, luminosity in [
            ("B6Ib", "B", "Supergiants"), ("G2V", "G", "Main Sequence"),
            ("A0IV", "A", "Subgiants"), ("M3.4III", "M", "Giants"),
            ("A(1)(III)", "A", "Giants"), ("F4Vn", "F", "Main Sequence"),
            ("OB-", "OB", None), ("B8", "B", None),
            ("A1II/III", "A", None), ("F2IV/V", "F", None),
            ("B2II", "B", None), ("G2VI", "G", None),
            ("DA2", None, "White Dwarfs"), ("sdB", None, None),
            ("B9/A0", None, None), ("B2V+F5V", None, None), ("C", None, None),
            (None, None, None),
        ]:
            with self.subTest(text=text):
                self.assertEqual(literature_spectral_type(text), spectral)
                self.assertEqual(literature_luminosity_class(text), luminosity)

    def test_exact_gaia_match_precedes_position_and_preserves_provenance(self):
        star = row(ra_deg=116)
        self.assertEqual(apply_literature_types([star], [record()]), (1, 0))
        self.assertEqual(star.literature_spectral_type, "B6Ib")
        self.assertEqual(star.spectral_type_reference, "1988MSS...C04....0H")
        self.assertEqual(star.spectral_type_match_method, "Gaia DR3 identifier")

    def test_position_match_propagates_gaia_epoch_and_requires_proper_motion(self):
        star = row(dec_deg=-15 + 16 * 100 / 3_600_000, pm_dec_mas_per_year=100)
        self.assertEqual(apply_literature_types([star], [record(ids="HD 61954")]), (0, 1))
        self.assertIn("J2000", star.spectral_type_match_method)
        self.assertEqual(apply_literature_types([row(pm_dec_mas_per_year=None)], [record(ids="")]), (0, 0))

    def test_conflicting_ids_and_ambiguous_neighbors_are_rejected(self):
        self.assertEqual(apply_literature_types([row()], [record(ids="Gaia DR3 2")]), (0, 0))
        self.assertEqual(apply_literature_types([row()], [record(ids=""), record(ids="")]), (0, 0))
        self.assertEqual(apply_literature_types([row(), row("2")], [record(ids="")]), (0, 0))
        self.assertEqual(apply_literature_types([row()], [record(), record()]), (0, 0))

    def test_field_query_requests_provenance_and_detects_truncation(self):
        response = Mock()
        columns = list(record())
        response.json.return_value = {"metadata": [{"name": c} for c in columns], "data": [list(record().values())]}
        with patch("photometry_app.core.hr_spectral_types.requests.get", return_value=response) as get:
            self.assertEqual(_query_field(115, -15, 0.8), [record()])
        self.assertIn("sp_bibcode", get.call_args.kwargs["params"]["query"])
        self.assertEqual(get.call_args.kwargs["timeout"], (5, 20))
        with patch("photometry_app.core.hr_spectral_types._MAX_ROWS", 0), patch("photometry_app.core.hr_spectral_types.requests.get", return_value=response):
            with self.assertRaises(ValueError):
                _query_field(115, -15, 0.8)

    def test_cache_reuse_empty_results_stale_fallback_and_retry_after_failure(self):
        field = SimpleNamespace(center_ra_deg=115, center_dec_deg=-15, radius_deg=0.8)
        with tempfile.TemporaryDirectory() as directory:
            cache = Path(directory)
            messages = []
            with patch("photometry_app.core.hr_spectral_types._query_field", return_value=[record()]) as query:
                enrich_hr_spectral_types([row()], field, cache, messages.append)
                enrich_hr_spectral_types([row()], field, cache, messages.append)
                query.assert_called_once()
            path = next(cache.rglob("*.json"))
            payload = json.loads(path.read_text())
            payload["fetched_at"] = 0
            path.write_text(json.dumps(payload))
            star = row()
            with patch("photometry_app.core.hr_spectral_types._query_field", side_effect=requests.Timeout):
                enrich_hr_spectral_types([star], field, cache, messages.append)
            self.assertEqual(star.literature_spectral_type, "B6Ib")
            self.assertTrue(any("older cached" in message for message in messages))
            path.unlink()
            with patch("photometry_app.core.hr_spectral_types._query_field", side_effect=requests.Timeout):
                enrich_hr_spectral_types([row()], field, cache, messages.append)
            self.assertFalse(path.exists())
            with patch("photometry_app.core.hr_spectral_types._query_field", return_value=[]) as query:
                enrich_hr_spectral_types([row()], field, cache, messages.append)
                enrich_hr_spectral_types([row()], field, cache, messages.append)
                query.assert_called_once()

    def test_corrupt_cache_is_refetched(self):
        field = SimpleNamespace(center_ra_deg=115, center_dec_deg=-15, radius_deg=0.8)
        with tempfile.TemporaryDirectory() as directory:
            cache = Path(directory)
            with patch("photometry_app.core.hr_spectral_types._query_field", return_value=[record()]):
                enrich_hr_spectral_types([row()], field, cache)
            next(cache.rglob("*.json")).write_text('{"fetched_at": 10000000000, "records": [null]}')
            with patch("photometry_app.core.hr_spectral_types._query_field", return_value=[record()]) as query:
                enrich_hr_spectral_types([row()], field, cache)
                query.assert_called_once()
