from __future__ import annotations



from io import BytesIO

import math

import tempfile

import unittest

from datetime import UTC, datetime, timedelta

from pathlib import Path

from types import SimpleNamespace

from unittest.mock import patch



import numpy as np

import requests

import astropy.units as u

from astropy.table import QTable, Table

from astropy.coordinates import EarthLocation

from astropy.io.fits import Header

from astropy.time import Time

from astropy.wcs import WCS



from photometry_app.core.models import CatalogStar, PlateSolveResult, SolvedField, WcsStatus

from photometry_app.core.settings import AppSettings

from photometry_app.core.solar_system import (

    HeliocentricReferenceBody,

    SkybotQueryService,

    SolarSystemDetection,

    SolarSystemFrameMeasurement,

    SolarSystemPrediction,

    SolarSystemStateVectorSample,

    _cached_known_small_body_prediction,

    _cached_small_body_search_candidates,

    _cached_known_interstellar_prediction,

    _altitude_deg,

    build_known_object_heliocentric_context,

    build_multi_known_object_heliocentric_context,

    _predictions_from_skybot_table,

    _query_horizons_ephemeris_magnitude_samples_chunk,

    _query_major_planet_heliocentric_paths,

    _query_known_interstellar_prediction,

    _query_known_small_body_prediction,

    _query_known_small_body_predictions,

    _resolve_source_field,

    search_bright_solar_system_objects_globally,

    detect_known_solar_system_objects,

    estimate_visible_magnitude_limit,

    lookup_known_solar_system_object,

    measure_detections_in_frame,

    measure_detection_in_frame,

    parse_observation_time,

    search_known_solar_system_objects,

    search_known_solar_system_objects_globally,

    search_nearby_known_solar_system_objects,

)




class _StubQueryService:

    def __init__(self, predictions: list[SolarSystemPrediction]) -> None:

        self._predictions = predictions



    def query_predictions(

        self,

        solved_field: SolvedField,

        observation_time: datetime,

        *,

        observatory_code: str | None = None,

        magnitude_limit: float | None = None,

    ) -> list[SolarSystemPrediction]:

        return list(self._predictions)





class _StubGaiaCatalogService:

    def __init__(self, stars: list[CatalogStar]) -> None:

        self._stars = stars



    def query_gaia_stars(self, solved_field: SolvedField, progress_callback=None) -> list[CatalogStar]:

        return list(self._stars)





class SolarSystemTest(unittest.TestCase):

    def test_resolve_source_field_uses_local_gaia_fallback_without_astrometry_key(self) -> None:

        with tempfile.TemporaryDirectory() as temp_dir:

            root_path = Path(temp_dir)

            source_path = root_path / "unsolved.fits"

            source_path.write_bytes(b"demo")

            settings = AppSettings.from_root(root_path)

            settings.astrometry_api_key = None

            solved_path = root_path / "local-gaia-solved.fits"

            solved_field = SolvedField(10.0, 20.0, 1.0, 100, 80, solved_path)

            local_result = PlateSolveResult(

                source_path=source_path,

                status=WcsStatus.SOLVED,

                solved_field=solved_field,

                reasons=[],

            )



            with (

                patch("photometry_app.core.solar_system.read_header_and_shape", return_value=(Header(), 100, 80)),

                patch("photometry_app.core.solar_system.validate_wcs", return_value=(False, ["Missing WCS."])),

                patch("photometry_app.core.solar_system.solve_wcs_from_metadata_and_gaia", return_value=local_result) as local_solver,

                patch("photometry_app.core.solar_system.AstrometryNetClient") as astrometry_client,

            ):

                resolved_field, used_fallback = _resolve_source_field(source_path, settings)



        self.assertEqual(resolved_field, solved_field)

        self.assertTrue(used_fallback)

        local_solver.assert_called_once_with(

            source_path,

            settings.cache_dir / "solar-system-wcs",

            progress_callback=None,

        )

        astrometry_client.assert_not_called()



    def test_parse_observation_time_uses_fallback_timezone_for_naive_input(self) -> None:

        parsed = parse_observation_time("2025-01-14T21:12:00", fallback_timezone="America/New_York")



        self.assertEqual(parsed, datetime(2025, 1, 15, 2, 12, tzinfo=UTC))



    def test_predictions_from_skybot_table_classifies_comets_and_filters_faint_rows(self) -> None:

        table = Table(

            rows=[

                ("C/2023 A3", "comet", 10.0, 20.0, 8.5, 3.0, 4.0, 1.2),

                ("(12345) Demo", "mb", 11.0, 21.0, 20.5, 1.0, 1.0, 0.8),

            ],

            names=("Name", "Type", "RA", "DEC", "V", "RA_rate", "DEC_rate", "posunc"),

        )


        predictions = _predictions_from_skybot_table(table, magnitude_limit=18.0)



        self.assertEqual(len(predictions), 1)

        self.assertEqual(predictions[0].object_type, "Comet")

        self.assertEqual(predictions[0].orbit_class, "comet")



    def test_predictions_from_skybot_qtable_accept_unit_bearing_columns(self) -> None:

        table = QTable()

        table["Name"] = ["(12345) Demo"]

        table["Type"] = ["MB>Inner"]

        table["RA"] = [10.0] * u.deg

        table["DEC"] = [20.0] * u.deg

        table["V"] = [14.2] * u.mag

        table["RA_rate"] = [3.0] * (u.arcsec / u.hour)

        table["DEC_rate"] = [4.0] * (u.arcsec / u.hour)

        table["posunc"] = [0.7] * u.arcsec



        predictions = _predictions_from_skybot_table(table, magnitude_limit=18.0)



        self.assertEqual(len(predictions), 1)

        self.assertAlmostEqual(predictions[0].ra_deg, 10.0)

        self.assertAlmostEqual(predictions[0].dec_deg, 20.0)

        self.assertAlmostEqual(predictions[0].ra_rate_arcsec_per_hour or 0.0, 3.0)

        self.assertAlmostEqual(predictions[0].dec_rate_arcsec_per_hour or 0.0, 4.0)



    def test_predictions_from_skybot_table_keeps_interstellar_comets_past_asteroid_mag_cut(self) -> None:

        table = Table(

            rows=[

                ("3I/ATLAS", "Interstellar", 10.0, 20.0, 21.2, 3.0, 4.0, 6.0),

                ("(12345) Demo", "MB>Inner", 11.0, 21.0, 21.2, 1.0, 1.0, 0.8),

            ],

            names=("Name", "Type", "RA", "DEC", "V", "RA_rate", "DEC_rate", "posunc"),

        )



        predictions = _predictions_from_skybot_table(table, magnitude_limit=18.0)



        self.assertEqual(len(predictions), 1)

        self.assertEqual(predictions[0].name, "3I/ATLAS")

        self.assertEqual(predictions[0].object_type, "Comet")

        self.assertEqual(predictions[0].orbit_class, "interstellar")



    def test_predictions_from_skybot_table_combines_split_interstellar_designation_and_name(self) -> None:

        table = Table(

            rows=[

                ("3I", "ATLAS", "Interstellar", 10.0, 20.0, 21.2, 3.0, 4.0, 6.0),

            ],

            names=("Number", "Name", "Type", "RA", "DEC", "V", "RA_rate", "DEC_rate", "posunc"),

        )



        predictions = _predictions_from_skybot_table(table, magnitude_limit=18.0)



        self.assertEqual(len(predictions), 1)

        self.assertEqual(predictions[0].name, "3I/ATLAS")

        self.assertEqual(predictions[0].designation, "3I")

        self.assertEqual(predictions[0].object_type, "Comet")

        self.assertEqual(predictions[0].orbit_class, "interstellar")



    def test_skybot_query_service_requests_comets_with_relaxed_position_error(self) -> None:

        solved_field = SolvedField(

            center_ra_deg=10.0,

            center_dec_deg=20.0,

            radius_deg=0.2,

            width=200,

            height=120,

            wcs_path=Path("demo.fits"),

        )

        empty_results = Table(

            names=("num", "name", "ra", "de", "class"),

            dtype=("U8", "U32", "U16", "f8", "U16"),

        )

        votable = BytesIO()

        empty_results.write(votable, format="votable")

        response = unittest.mock.Mock()

        response.content = votable.getvalue()

        response.text = response.content.decode("utf-8")

        response.raise_for_status.return_value = None



        with (

            patch("photometry_app.core.solar_system.requests.get", return_value=response) as request_mock,

            patch("photometry_app.core.solar_system._query_known_interstellar_prediction", return_value=None),

        ):

            predictions = SkybotQueryService().query_predictions(

                solved_field,

                datetime(2025, 1, 14, 21, 12, tzinfo=UTC),

                observatory_code="807",

                magnitude_limit=18.0,

            )



        self.assertEqual(predictions, [])

        request_mock.assert_called_once()

        kwargs = request_mock.call_args.kwargs

        self.assertEqual(kwargs["params"]["-filter"], 3600.0)

        self.assertEqual(kwargs["params"]["-loc"], "807")

        self.assertEqual(kwargs["params"]["-objFilter"], "101")

    def test_skybot_query_service_retries_transient_http_500_and_succeeds(self) -> None:

        solved_field = SolvedField(

            center_ra_deg=10.0,

            center_dec_deg=20.0,

            radius_deg=0.2,

            width=200,

            height=120,

            wcs_path=Path("demo.fits"),

        )

        empty_results = Table(

            names=("num", "name", "ra", "de", "class"),

            dtype=("U8", "U32", "U16", "f8", "U16"),

        )

        votable = BytesIO()

        empty_results.write(votable, format="votable")

        failed_response = unittest.mock.Mock()

        failed_response.status_code = 500

        transient_error = requests.HTTPError("500 Server Error: Internal Server Error")

        transient_error.response = failed_response

        failed_response.raise_for_status.side_effect = transient_error

        success_response = unittest.mock.Mock()

        success_response.content = votable.getvalue()

        success_response.text = success_response.content.decode("utf-8")

        success_response.raise_for_status.return_value = None

        with (

            patch("photometry_app.core.solar_system.requests.get", side_effect=[failed_response, success_response]) as request_mock,

            patch("photometry_app.core.solar_system._query_known_interstellar_prediction", return_value=None),

        ):

            predictions = SkybotQueryService().query_predictions(

                solved_field,

                datetime(2025, 1, 14, 21, 12, tzinfo=UTC),

                observatory_code="807",

                magnitude_limit=18.0,

            )

        self.assertEqual(predictions, [])

        self.assertEqual(request_mock.call_count, 2)

    def test_skybot_query_service_accepts_votable_with_leading_whitespace(self) -> None:

        solved_field = SolvedField(

            center_ra_deg=10.0,

            center_dec_deg=20.0,

            radius_deg=0.2,

            width=200,

            height=120,

            wcs_path=Path("demo.fits"),

        )

        empty_results = Table(

            names=("num", "name", "ra", "de", "class"),

            dtype=("U8", "U32", "U16", "f8", "U16"),

        )

        votable = BytesIO()

        empty_results.write(votable, format="votable")

        response = unittest.mock.Mock()

        response.content = b"\n   " + votable.getvalue()

        response.text = response.content.decode("utf-8")

        response.raise_for_status.return_value = None

        with (

            patch("photometry_app.core.solar_system.requests.get", return_value=response),

            patch("photometry_app.core.solar_system._query_known_interstellar_prediction", return_value=None),

        ):

            predictions = SkybotQueryService().query_predictions(

                solved_field,

                datetime(2025, 1, 14, 21, 12, tzinfo=UTC),

                observatory_code="807",

                magnitude_limit=18.0,

            )

        self.assertEqual(predictions, [])

    def test_search_nearby_known_solar_system_objects_keeps_off_image_predictions(self) -> None:

        solved_field = SolvedField(

            center_ra_deg=10.0,

            center_dec_deg=20.0,

            radius_deg=0.2,

            width=200,

            height=120,

            wcs_path=Path("demo.fits"),

        )

        wcs = WCS(naxis=2)

        wcs.wcs.crpix = [100.0, 60.0]

        wcs.wcs.cdelt = np.array([-0.01, 0.01])

        wcs.wcs.crval = [10.0, 20.0]

        wcs.wcs.ctype = ["RA---TAN", "DEC--TAN"]

        predictions = [

            SolarSystemPrediction(

                name="(1) Ceres",

                designation="1",

                object_type="Asteroid",

                orbit_class="main-belt",

                ra_deg=10.0,

                dec_deg=20.0,

                predicted_magnitude=12.3,

                ra_rate_arcsec_per_hour=3.0,

                dec_rate_arcsec_per_hour=0.0,

            ),

            SolarSystemPrediction(

                name="(2) Pallas",

                designation="2",

                object_type="Asteroid",

                orbit_class="main-belt",

                ra_deg=11.6,

                dec_deg=20.0,

                predicted_magnitude=13.8,

                ra_rate_arcsec_per_hour=2.0,

                dec_rate_arcsec_per_hour=0.5,

            ),

        ]

        with patch("photometry_app.core.solar_system.read_header", return_value=wcs.to_header()):

            results = search_nearby_known_solar_system_objects(

                solved_field,

                observation_time=datetime(2025, 1, 14, 21, 12, tzinfo=UTC),

                exposure_seconds=120.0,

                observer_latitude_deg=None,

                observer_longitude_deg=None,

                observer_elevation_m=None,

                search_radius_deg=3.0,

                magnitude_limit=18.0,

                query_service=_StubQueryService(predictions),

            )

        self.assertEqual(len(results), 2)
        by_name = {item.detection.name: item for item in results}
        self.assertTrue(by_name["(1) Ceres"].is_in_image)
        self.assertEqual(by_name["(1) Ceres"].detection.status, "Predicted in field")
        self.assertFalse(by_name["(2) Pallas"].is_in_image)
        self.assertEqual(by_name["(2) Pallas"].detection.status, "Outside image")

    def test_lookup_known_solar_system_object_keeps_off_image_exact_match(self) -> None:

        solved_field = SolvedField(

            center_ra_deg=10.0,

            center_dec_deg=20.0,

            radius_deg=0.2,

            width=200,

            height=120,

            wcs_path=Path("demo.fits"),

        )

        wcs = WCS(naxis=2)

        wcs.wcs.crpix = [100.0, 60.0]

        wcs.wcs.cdelt = np.array([-0.01, 0.01])

        wcs.wcs.crval = [10.0, 20.0]

        wcs.wcs.ctype = ["RA---TAN", "DEC--TAN"]

        prediction = SolarSystemPrediction(

            name="(2) Pallas",

            designation="2",

            object_type="Asteroid",

            orbit_class="main-belt",

            ra_deg=11.6,

            dec_deg=20.0,

            predicted_magnitude=13.8,

            ra_rate_arcsec_per_hour=2.0,

            dec_rate_arcsec_per_hour=0.5,

        )

        with (

            patch("photometry_app.core.solar_system.read_header", return_value=wcs.to_header()),

            patch("photometry_app.core.solar_system._query_known_small_body_prediction", return_value=prediction),

        ):

            result = lookup_known_solar_system_object(

                "Pallas",

                solved_field,

                observation_time=datetime(2025, 1, 14, 21, 12, tzinfo=UTC),

                exposure_seconds=120.0,

                observer_latitude_deg=None,

                observer_longitude_deg=None,

                observer_elevation_m=None,

                magnitude_limit=18.0,

            )

        self.assertIsNotNone(result)
        assert result is not None
        self.assertFalse(result.is_in_image)
        self.assertEqual(result.detection.name, "(2) Pallas")
        self.assertEqual(result.detection.status, "Outside image")

    def test_search_known_solar_system_objects_returns_multiple_keyword_matches(self) -> None:

        solved_field = SolvedField(

            center_ra_deg=10.0,

            center_dec_deg=20.0,

            radius_deg=0.2,

            width=200,

            height=120,

            wcs_path=Path("demo.fits"),

        )

        wcs = WCS(naxis=2)

        wcs.wcs.crpix = [100.0, 60.0]

        wcs.wcs.cdelt = np.array([-0.01, 0.01])

        wcs.wcs.crval = [10.0, 20.0]

        wcs.wcs.ctype = ["RA---TAN", "DEC--TAN"]

        predictions = {
            "2 Pallas": SolarSystemPrediction(
                name="(2) Pallas",
                designation="2",
                object_type="Asteroid",
                orbit_class="main-belt",
                ra_deg=11.6,
                dec_deg=20.0,
                predicted_magnitude=13.8,
                ra_rate_arcsec_per_hour=2.0,
                dec_rate_arcsec_per_hour=0.5,
            ),
            "21087 Petsimpallas": SolarSystemPrediction(
                name="(21087) Petsimpallas",
                designation="21087",
                object_type="Asteroid",
                orbit_class="main-belt",
                ra_deg=9.4,
                dec_deg=20.2,
                predicted_magnitude=16.2,
                ra_rate_arcsec_per_hour=1.5,
                dec_rate_arcsec_per_hour=0.3,
            ),
        }

        with (

            patch("photometry_app.core.solar_system.read_header", return_value=wcs.to_header()),

            patch(
                "photometry_app.core.solar_system._search_known_small_body_candidates",
                return_value=("2 Pallas", "21087 Petsimpallas"),
            ),

            patch(
                "photometry_app.core.solar_system._query_known_small_body_prediction",
                side_effect=lambda identifier, observation_time, observatory_code=None: predictions.get(identifier),
            ),

        ):

            results = search_known_solar_system_objects(

                "Pallas",

                solved_field,

                observation_time=datetime(2025, 1, 14, 21, 12, tzinfo=UTC),

                exposure_seconds=120.0,

                observer_latitude_deg=None,

                observer_longitude_deg=None,

                observer_elevation_m=None,

                magnitude_limit=18.0,

            )

        self.assertEqual(len(results), 2)
        self.assertEqual([item.detection.name for item in results], ["(2) Pallas", "(21087) Petsimpallas"])

    def test_search_known_solar_system_objects_keeps_outside_field_matches(self) -> None:

        solved_field = SolvedField(

            center_ra_deg=10.0,

            center_dec_deg=20.0,

            radius_deg=0.2,

            width=200,

            height=120,

            wcs_path=Path("demo.fits"),

        )

        prediction = SolarSystemPrediction(
            name="154P/Brewington",
            designation="154P",
            object_type="Comet",
            orbit_class="JFC",
            ra_deg=150.0,
            dec_deg=-30.0,
            predicted_magnitude=17.1,
            ra_rate_arcsec_per_hour=1.2,
            dec_rate_arcsec_per_hour=-0.3,
        )

        class _UnprojectableWcs:
            def world_to_pixel_values(self, ra_deg: float, dec_deg: float) -> tuple[float, float]:
                return float("nan"), float("nan")

        with (
            patch(
                "photometry_app.core.solar_system._search_known_small_body_candidates",
                return_value=("154P/Brewington",),
            ),
            patch(
                "photometry_app.core.solar_system._query_known_small_body_prediction",
                return_value=prediction,
            ),
            patch("photometry_app.core.solar_system._resolved_wcs_for_field", return_value=_UnprojectableWcs()),
            patch("photometry_app.core.solar_system._pixel_scale_arcsec_per_pixel", return_value=None),
        ):

            results = search_known_solar_system_objects(
                "154P",
                solved_field,
                observation_time=datetime(2025, 1, 14, 21, 12, tzinfo=UTC),
                exposure_seconds=120.0,
                observer_latitude_deg=None,
                observer_longitude_deg=None,
                observer_elevation_m=None,
                magnitude_limit=18.0,
            )

        self.assertEqual(len(results), 1)
        self.assertEqual(results[0].detection.status, "Outside field")
        self.assertFalse(results[0].is_in_image)
        self.assertTrue(math.isnan(results[0].detection.predicted_x))
        self.assertTrue(math.isnan(results[0].detection.predicted_y))

    def test_search_known_solar_system_objects_globally_returns_planning_candidates(self) -> None:

        predictions = {
            "2 Pallas": SolarSystemPrediction(
                name="(2) Pallas",
                designation="2",
                object_type="Asteroid",
                orbit_class="main-belt",
                ra_deg=11.6,
                dec_deg=20.0,
                predicted_magnitude=13.8,
                ra_rate_arcsec_per_hour=2.0,
                dec_rate_arcsec_per_hour=0.5,
            ),
            "21087 Petsimpallas": SolarSystemPrediction(
                name="(21087) Petsimpallas",
                designation="21087",
                object_type="Asteroid",
                orbit_class="main-belt",
                ra_deg=9.4,
                dec_deg=20.2,
                predicted_magnitude=16.2,
                ra_rate_arcsec_per_hour=1.5,
                dec_rate_arcsec_per_hour=0.3,
            ),
        }

        with (
            patch(
                "photometry_app.core.solar_system._search_known_small_body_candidates",
                return_value=("2 Pallas", "21087 Petsimpallas"),
            ),
            patch(
                "photometry_app.core.solar_system._query_known_small_body_prediction",
                side_effect=lambda identifier, observation_time, observatory_code=None: predictions.get(identifier),
            ),
        ):

            results = search_known_solar_system_objects_globally(
                "Pallas",
                observation_time=datetime(2026, 4, 22, 0, 0, tzinfo=UTC),
                observer_latitude_deg=None,
                observer_longitude_deg=None,
                observer_elevation_m=None,
            )

        self.assertEqual(len(results), 2)
        self.assertEqual(results[0].angular_distance_deg, None)
        self.assertEqual(results[0].detection.status, "Planning candidate")
        self.assertFalse(results[0].is_in_image)

    def test_query_known_small_body_predictions_uses_configured_parallel_worker_limit(self) -> None:

        identifiers = tuple(f"object-{index}" for index in range(5))
        submitted_identifiers: list[str] = []
        recorded_max_workers: list[int] = []

        class _FakeFuture:
            def __init__(self, value: SolarSystemPrediction | None) -> None:
                self._value = value

            def result(self) -> SolarSystemPrediction | None:
                return self._value

        class _FakeExecutor:
            def __init__(self, *, max_workers: int) -> None:
                recorded_max_workers.append(max_workers)

            def __enter__(self) -> "_FakeExecutor":
                return self

            def __exit__(self, exc_type, exc, tb) -> bool:
                return False

            def submit(self, func, identifier, observation_time, observatory_code=None):
                submitted_identifiers.append(identifier)
                return _FakeFuture(func(identifier, observation_time, observatory_code=observatory_code))

        predictions = {
            identifier: SolarSystemPrediction(
                name=identifier,
                designation=identifier,
                object_type="Asteroid",
                orbit_class="main-belt",
                ra_deg=10.0,
                dec_deg=20.0,
                predicted_magnitude=12.0,
                ra_rate_arcsec_per_hour=0.5,
                dec_rate_arcsec_per_hour=0.1,
            )
            for identifier in identifiers
        }

        with (
            patch("photometry_app.core.solar_system.ThreadPoolExecutor", _FakeExecutor),
            patch("photometry_app.core.solar_system.as_completed", side_effect=lambda futures: futures),
            patch(
                "photometry_app.core.solar_system._query_known_small_body_prediction",
                side_effect=lambda identifier, observation_time, observatory_code=None: predictions[identifier],
            ),
        ):
            results = _query_known_small_body_predictions(
                identifiers,
                datetime(2026, 4, 22, 0, 0, tzinfo=UTC),
                observatory_code=None,
                max_parallel_workers=3,
            )

        self.assertEqual(recorded_max_workers, [3])
        self.assertEqual(submitted_identifiers, list(identifiers))
        self.assertEqual(len(results), len(identifiers))

    def test_search_bright_solar_system_objects_globally_uses_configured_parallel_worker_limit(self) -> None:

        tile_centers = tuple((float(index), float(index)) for index in range(6))
        recorded_max_workers: list[int] = []

        class _FakeFuture:
            def __init__(self, value: list[SolarSystemPrediction]) -> None:
                self._value = value

            def result(self) -> list[SolarSystemPrediction]:
                return self._value

        class _FakeExecutor:
            def __init__(self, *, max_workers: int) -> None:
                recorded_max_workers.append(max_workers)

            def __enter__(self) -> "_FakeExecutor":
                return self

            def __exit__(self, exc_type, exc, tb) -> bool:
                return False

            def submit(self, func, ra_deg, dec_deg, observation_time, observatory_code=None, include_asteroids=True, include_comets=True):
                return _FakeFuture(
                    func(
                        ra_deg,
                        dec_deg,
                        observation_time,
                        observatory_code=observatory_code,
                        include_asteroids=include_asteroids,
                        include_comets=include_comets,
                    )
                )

        with (
            patch("photometry_app.core.solar_system.Skybot", object()),
            patch("photometry_app.core.solar_system.skybot_conf", object()),
            patch("photometry_app.core.solar_system._global_bright_skybot_tile_centers", return_value=tile_centers),
            patch("photometry_app.core.solar_system.ThreadPoolExecutor", _FakeExecutor),
            patch("photometry_app.core.solar_system.as_completed", side_effect=lambda futures: futures),
            patch("photometry_app.core.solar_system._query_global_bright_skybot_tile_predictions", return_value=[]),
        ):
            results = search_bright_solar_system_objects_globally(
                observation_time=datetime(2026, 4, 22, 0, 0, tzinfo=UTC),
                observer_latitude_deg=None,
                observer_longitude_deg=None,
                observer_elevation_m=None,
                max_parallel_workers=4,
            )

        self.assertEqual(recorded_max_workers, [4])
        self.assertEqual(results, [])

    def test_search_bright_solar_system_objects_globally_reuses_local_snapshot_when_available(self) -> None:

        prediction = SolarSystemPrediction(
            name="Bright Comet",
            designation="C/2026 B1",
            object_type="Comet",
            orbit_class="comet",
            ra_deg=10.0,
            dec_deg=20.0,
            predicted_magnitude=8.4,
            ra_rate_arcsec_per_hour=0.5,
            dec_rate_arcsec_per_hour=0.2,
            positional_uncertainty_arcsec=0.8,
        )

        with tempfile.TemporaryDirectory() as temp_dir:
            cache_dir = Path(temp_dir)
            with (
                patch("photometry_app.core.solar_system.Skybot", object()),
                patch("photometry_app.core.solar_system.skybot_conf", object()),
                patch("photometry_app.core.solar_system._global_bright_skybot_tile_centers", return_value=((0.0, 0.0),)),
                patch("photometry_app.core.solar_system._query_global_bright_skybot_tile_predictions", return_value=[prediction]),
            ):
                live_results = search_bright_solar_system_objects_globally(
                    observation_time=datetime(2026, 4, 22, 0, 0, tzinfo=UTC),
                    observer_latitude_deg=None,
                    observer_longitude_deg=None,
                    observer_elevation_m=None,
                    cache_dir=cache_dir,
                    use_local_database=False,
                )

            with (
                patch("photometry_app.core.solar_system.Skybot", object()),
                patch("photometry_app.core.solar_system.skybot_conf", object()),
                patch(
                    "photometry_app.core.solar_system._query_global_bright_skybot_tile_predictions",
                    side_effect=AssertionError("Live scan should not run when local snapshot coverage exists."),
                ),
            ):
                cached_results = search_bright_solar_system_objects_globally(
                    observation_time=datetime(2026, 4, 22, 6, 0, tzinfo=UTC),
                    observer_latitude_deg=None,
                    observer_longitude_deg=None,
                    observer_elevation_m=None,
                    cache_dir=cache_dir,
                )

        self.assertEqual([result.detection.name for result in live_results], ["Bright Comet"])
        self.assertEqual([result.detection.name for result in cached_results], ["Bright Comet"])

    def test_search_bright_solar_system_objects_globally_falls_back_live_when_snapshot_missing(self) -> None:

        prediction = SolarSystemPrediction(
            name="Bright Asteroid",
            designation="12345",
            object_type="Asteroid",
            orbit_class="main-belt",
            ra_deg=11.0,
            dec_deg=21.0,
            predicted_magnitude=7.9,
            ra_rate_arcsec_per_hour=0.5,
            dec_rate_arcsec_per_hour=0.2,
            positional_uncertainty_arcsec=0.8,
        )

        with tempfile.TemporaryDirectory() as temp_dir:
            cache_dir = Path(temp_dir)
            with (
                patch("photometry_app.core.solar_system.Skybot", object()),
                patch("photometry_app.core.solar_system.skybot_conf", object()),
                patch("photometry_app.core.solar_system._global_bright_skybot_tile_centers", return_value=((0.0, 0.0),)),
                patch("photometry_app.core.solar_system._query_global_bright_skybot_tile_predictions", return_value=[prediction]) as live_query,
            ):
                results = search_bright_solar_system_objects_globally(
                    observation_time=datetime(2026, 4, 22, 0, 0, tzinfo=UTC),
                    observer_latitude_deg=None,
                    observer_longitude_deg=None,
                    observer_elevation_m=None,
                    cache_dir=cache_dir,
                )

        self.assertEqual(live_query.call_count, 1)
        self.assertEqual([result.detection.name for result in results], ["Bright Asteroid"])

    def test_cached_small_body_search_candidates_uses_prefix_wildcard_fallback(self) -> None:

        _cached_small_body_search_candidates.cache_clear()

        with patch(
            "photometry_app.core.solar_system.SBDB.query",
            side_effect=(
                {"message": "No match", "code": 200, "moreInfo": ""},
                {
                    "list": {
                        "name": ["C/2025 N1 (ATLAS)", "C/2025 A1 (Lemmon)"],
                        "pdes": ["C/2025 N1", "C/2025 A1"],
                    }
                },
            ),
        ) as query:

            candidates = _cached_small_body_search_candidates("C/2025", 5)

        self.assertEqual([call.args[0] for call in query.call_args_list], ["C/2025", "C/2025*"])
        self.assertEqual(candidates[:2], ("C/2025 N1", "C/2025 A1"))

    def test_cached_small_body_search_candidates_preserve_comet_prefix_from_name(self) -> None:

        _cached_small_body_search_candidates.cache_clear()

        with patch(
            "photometry_app.core.solar_system.SBDB.query",
            return_value={
                "list": {
                    "name": ["C/2025 Q3 (ATLAS)", "C/2025 R3 (PANSTARRS)"],
                    "pdes": ["2025 Q3", "2025 R3"],
                }
            },
        ):
            candidates = _cached_small_body_search_candidates("C/2025", 5)

        self.assertEqual(candidates[:2], ("C/2025 Q3", "C/2025 R3"))

    def test_cached_small_body_search_candidates_merge_exact_and_wildcard_results(self) -> None:

        _cached_small_body_search_candidates.cache_clear()

        with patch(
            "photometry_app.core.solar_system.SBDB.query",
            side_effect=(
                {
                    "list": {
                        "name": ["C/2025 N1 (ATLAS)", "C/2025 A1 (Lemmon)"],
                        "pdes": ["C/2025 N1", "C/2025 A1"],
                    }
                },
                {
                    "list": {
                        "name": ["C/2025 R3 (PANSTARRS)", "C/2025 A1 (Lemmon)"],
                        "pdes": ["2025 R3", "C/2025 A1"],
                    }
                },
            ),
        ) as query:

            candidates = _cached_small_body_search_candidates("C/2025", 5)

        self.assertEqual([call.args[0] for call in query.call_args_list], ["C/2025", "C/2025*"])
        self.assertEqual(candidates[:3], ("C/2025 N1", "C/2025 A1", "C/2025 R3"))

    def test_cached_known_small_body_prediction_classifies_comet_from_embedded_designation(self) -> None:

        _cached_known_small_body_prediction.cache_clear()

        table = Table(
            rows=[("Lemmon (C/2025 A6)", 174.43865, 2.5974, 15.151, -149.235, 53.13054, 0.085)],
            names=("targetname", "RA", "DEC", "Tmag", "RA_rate", "DEC_rate", "SMAA_3sigma"),
        )

        class _FakeHorizons:

            def __init__(self, **_kwargs: object) -> None:
                pass

            def ephemerides(self) -> Table:
                return table

        with patch("photometry_app.core.solar_system.Horizons", _FakeHorizons):
            prediction = _cached_known_small_body_prediction(
                "C/2025 A6",
                "2026-04-01T17:19:59",
                "500",
            )

        self.assertIsNotNone(prediction)
        assert prediction is not None
        self.assertEqual(prediction.name, "Lemmon (C/2025 A6)")
        self.assertEqual(prediction.object_type, "Comet")
        self.assertEqual(prediction.orbit_class, "comet")

        _cached_known_small_body_prediction.cache_clear()

    def test_cached_known_small_body_prediction_retries_transient_failure_without_caching_miss(self) -> None:

        _cached_known_small_body_prediction.cache_clear()

        table = Table(
            rows=[("PANSTARRS (C/2025 R3)", 174.43865, 2.5974, 7.0, -149.235, 53.13054, 0.085)],
            names=("targetname", "RA", "DEC", "Tmag", "RA_rate", "DEC_rate", "SMAA_3sigma"),
        )

        class _FakeHorizons:

            call_count = 0

            def __init__(self, **_kwargs: object) -> None:
                pass

            def ephemerides(self):
                type(self).call_count += 1
                if type(self).call_count == 1:
                    raise RuntimeError("temporary failure")
                return table

        with patch("photometry_app.core.solar_system.Horizons", _FakeHorizons):
            first_prediction = _cached_known_small_body_prediction("C/2025 R3", "2026-04-22T18:00:54", "500")
            second_prediction = _cached_known_small_body_prediction("C/2025 R3", "2026-04-22T18:00:54", "500")

        self.assertIsNotNone(first_prediction)
        self.assertIsNotNone(second_prediction)
        assert first_prediction is not None
        self.assertEqual(first_prediction.name, "PANSTARRS (C/2025 R3)")
        self.assertEqual(_FakeHorizons.call_count, 2)

    def test_query_horizons_ephemeris_magnitude_samples_prefers_total_comet_magnitude(self) -> None:

        table = Table(
            rows=[("PANSTARRS (C/2025 R3)", 7.0, 18.0)],
            names=("targetname", "Tmag", "Nmag"),
        )

        class _FakeHorizons:

            def __init__(self, **_kwargs: object) -> None:
                pass

            def ephemerides(self) -> Table:
                return table

        with patch("photometry_app.core.solar_system.Horizons", _FakeHorizons):
            target_name, samples = _query_horizons_ephemeris_magnitude_samples_chunk(
                target_id="DES=C/2025 R3;CAP",
                query_times=(datetime(2026, 4, 25, tzinfo=UTC),),
                location_code="500@399",
            )

        self.assertEqual(target_name, "PANSTARRS (C/2025 R3)")
        self.assertEqual(len(samples), 1)
        self.assertEqual(samples[0].literature_magnitude, 7.0)

    def test_search_bright_solar_system_objects_globally_deduplicates_tiles_and_filters_types(self) -> None:

        comet_prediction = SolarSystemPrediction(
            name="Bright Comet",
            designation="C/2026 B1",
            object_type="Comet",
            orbit_class="comet",
            ra_deg=10.0,
            dec_deg=20.0,
            predicted_magnitude=8.5,
            ra_rate_arcsec_per_hour=0.5,
            dec_rate_arcsec_per_hour=0.2,
        )
        asteroid_prediction = SolarSystemPrediction(
            name="Bright Asteroid",
            designation="12345",
            object_type="Asteroid",
            orbit_class="main-belt",
            ra_deg=12.0,
            dec_deg=21.0,
            predicted_magnitude=7.2,
            ra_rate_arcsec_per_hour=0.5,
            dec_rate_arcsec_per_hour=0.2,
        )
        streamed_names: list[str] = []

        with (
            patch("photometry_app.core.solar_system.Skybot", object()),
            patch("photometry_app.core.solar_system.skybot_conf", SimpleNamespace(timeout=30.0)),
            patch(
                "photometry_app.core.solar_system._global_bright_skybot_tile_centers",
                return_value=((0.0, 0.0), (20.0, 0.0)),
            ),
            patch(
                "photometry_app.core.solar_system._query_global_bright_skybot_tile_predictions",
                side_effect=((comet_prediction, asteroid_prediction), (comet_prediction,)),
            ),
        ):
            results = search_bright_solar_system_objects_globally(
                observation_time=datetime(2026, 4, 22, 0, 0, tzinfo=UTC),
                observer_latitude_deg=None,
                observer_longitude_deg=None,
                observer_elevation_m=None,
                magnitude_limit=10.0,
                include_asteroids=False,
                include_comets=True,
                result_callback=lambda result: streamed_names.append(result.detection.name),
            )

        self.assertEqual([result.detection.name for result in results], ["Bright Comet"])
        self.assertEqual(streamed_names, ["Bright Comet"])

    def test_query_known_small_body_prediction_falls_back_to_geocentric_location(self) -> None:

        prediction = SolarSystemPrediction(
            name="(2) Pallas",
            designation="2",
            object_type="Asteroid",
            orbit_class="main-belt",
            ra_deg=11.6,
            dec_deg=20.0,
            predicted_magnitude=13.8,
            ra_rate_arcsec_per_hour=2.0,
            dec_rate_arcsec_per_hour=0.5,
        )

        def side_effect(identifier: str, observation_time_iso: str, location_code: str):
            if location_code == "OKDA":
                return None
            if location_code == "500":
                return prediction
            return None

        with patch(
            "photometry_app.core.solar_system._cached_known_small_body_prediction",
            side_effect=side_effect,
        ):
            result = _query_known_small_body_prediction(
                "2",
                datetime(2026, 4, 22, 0, 0, tzinfo=UTC),
                observatory_code="OKDA",
            )

        self.assertIs(result, prediction)

    def test_skybot_query_service_accepts_votable_with_proxy_preamble(self) -> None:

        solved_field = SolvedField(

            center_ra_deg=10.0,

            center_dec_deg=20.0,

            radius_deg=0.2,

            width=200,

            height=120,

            wcs_path=Path("demo.fits"),

        )

        empty_results = Table(

            names=("num", "name", "ra", "de", "class"),

            dtype=("U8", "U32", "U16", "f8", "U16"),

        )

        votable = BytesIO()

        empty_results.write(votable, format="votable")

        response = unittest.mock.Mock()

        response.content = b"proxy-note\r\n" + votable.getvalue()

        response.text = response.content.decode("utf-8")

        response.raise_for_status.return_value = None

        with (

            patch("photometry_app.core.solar_system.requests.get", return_value=response),

            patch("photometry_app.core.solar_system._query_known_interstellar_prediction", return_value=None),

        ):

            predictions = SkybotQueryService().query_predictions(

                solved_field,

                datetime(2025, 1, 14, 21, 12, tzinfo=UTC),

                observatory_code="807",

                magnitude_limit=18.0,

            )

        self.assertEqual(predictions, [])



    def test_skybot_query_service_supplements_missing_interstellar_object(self) -> None:

        solved_field = SolvedField(

            center_ra_deg=10.0,

            center_dec_deg=20.0,

            radius_deg=0.2,

            width=200,

            height=120,

            wcs_path=Path("demo.fits"),

        )



        with (

            patch("photometry_app.core.solar_system._skybot_cone_search_table", return_value=Table(names=("Name", "Type", "RA", "DEC"), rows=[])),

            patch(

                "photometry_app.core.solar_system._query_known_interstellar_prediction",

                side_effect=lambda object_name, observation_time, observatory_code=None, lookup_name=None: SolarSystemPrediction(

                    name="3I/ATLAS",

                    designation="3I",

                    object_type="Comet",

                    orbit_class="interstellar",

                    ra_deg=10.0,

                    dec_deg=20.0,

                    predicted_magnitude=21.2,

                    ra_rate_arcsec_per_hour=3.0,

                    dec_rate_arcsec_per_hour=4.0,

                    positional_uncertainty_arcsec=None,

                ) if object_name == "3I/ATLAS" else None,

            ),

        ):

            predictions = SkybotQueryService().query_predictions(

                solved_field,

                datetime(2025, 1, 14, 21, 12, tzinfo=UTC),

                observatory_code="807",

                magnitude_limit=18.0,

            )



        self.assertEqual(len(predictions), 1)

        self.assertEqual(predictions[0].name, "3I/ATLAS")

        self.assertEqual(predictions[0].orbit_class, "interstellar")



    def test_skybot_query_service_refreshes_existing_interstellar_object_with_targeted_fallback(self) -> None:

        solved_field = SolvedField(

            center_ra_deg=10.0,

            center_dec_deg=20.0,

            radius_deg=0.2,

            width=200,

            height=120,

            wcs_path=Path("demo.fits"),

        )

        skybot_table = Table(

            rows=[("3I", "Comet", 10.0, 20.0, 21.2, 3.0, 4.0, 6.0)],

            names=("Name", "Type", "RA", "DEC", "V", "RA_rate", "DEC_rate", "posunc"),

        )



        with (

            patch("photometry_app.core.solar_system._skybot_cone_search_table", return_value=skybot_table),

            patch(

                "photometry_app.core.solar_system._query_known_interstellar_prediction",

                return_value=SolarSystemPrediction(

                    name="3I/ATLAS",

                    designation="3I",

                    object_type="Comet",

                    orbit_class="interstellar",

                    ra_deg=10.0,

                    dec_deg=20.0,

                    predicted_magnitude=15.151,

                    ra_rate_arcsec_per_hour=3.0,

                    dec_rate_arcsec_per_hour=4.0,

                    positional_uncertainty_arcsec=6.0,

                ),

            ) as fallback_mock,

        ):

            predictions = SkybotQueryService().query_predictions(

                solved_field,

                datetime(2025, 1, 14, 21, 12, tzinfo=UTC),

                observatory_code="807",

                magnitude_limit=18.0,

            )



        self.assertEqual(len(predictions), 1)

        self.assertEqual(predictions[0].name, "3I/ATLAS")

        self.assertEqual(predictions[0].designation, "3I")

        self.assertAlmostEqual(predictions[0].predicted_magnitude or 0.0, 15.151)

        fallback_mock.assert_called_once()



    def test_query_known_interstellar_prediction_uses_astropy_compatible_utc_epoch(self) -> None:

        _cached_known_interstellar_prediction.cache_clear()

        stub_prediction = SolarSystemPrediction(

            name="3I/ATLAS",

            designation="3I",

            object_type="Comet",

            orbit_class="interstellar",

            ra_deg=10.0,

            dec_deg=20.0,

            predicted_magnitude=21.2,

            ra_rate_arcsec_per_hour=3.0,

            dec_rate_arcsec_per_hour=4.0,

            positional_uncertainty_arcsec=6.0,

        )



        with patch("photometry_app.core.solar_system._cached_known_interstellar_prediction", return_value=stub_prediction) as cached_mock:



            prediction = _query_known_interstellar_prediction(

                "3I/ATLAS",

                datetime(2025, 12, 7, 9, 1, 30, 500000, tzinfo=UTC),

                observatory_code=None,

                lookup_name="3I",

            )



        self.assertIsNotNone(prediction)

        assert prediction is not None

        self.assertEqual(prediction.name, "3I/ATLAS")

        args = cached_mock.call_args.args

        self.assertEqual(args[0], "3I/ATLAS")

        self.assertEqual(args[1], "3I")

        self.assertEqual(args[2], "2025-12-07T09:01:30")

        self.assertEqual(args[3], "500")

        _cached_known_interstellar_prediction.cache_clear()



    def test_cached_known_interstellar_prediction_prefers_horizons_before_miriade(self) -> None:

        _cached_known_interstellar_prediction.cache_clear()

        table = Table(

            rows=[("ATLAS (C/2025 N1)", 174.43865, 2.5974, 15.151, -149.235, 53.13054, 0.085)],

            names=("targetname", "RA", "DEC", "Tmag", "RA_rate", "DEC_rate", "SMAA_3sigma"),

        )



        with (

            patch("photometry_app.core.solar_system.Miriade") as miriade_mock,

            patch("photometry_app.core.solar_system.Horizons") as horizons_mock,

        ):

            horizons_mock.return_value.ephemerides.return_value = table



            prediction = _cached_known_interstellar_prediction(

                "3I/ATLAS",

                "3I",

                "2025-12-07T09:01:30",

                "500",

            )



        self.assertIsNotNone(prediction)

        assert prediction is not None

        self.assertEqual(prediction.name, "3I/ATLAS")

        self.assertEqual(prediction.designation, "3I")

        self.assertAlmostEqual(prediction.ra_deg, 174.43865)

        self.assertAlmostEqual(prediction.dec_deg, 2.5974)

        self.assertAlmostEqual(prediction.predicted_magnitude or 0.0, 15.151)

        self.assertAlmostEqual(prediction.ra_rate_arcsec_per_hour or 0.0, -149.235)

        self.assertAlmostEqual(prediction.dec_rate_arcsec_per_hour or 0.0, 53.13054)

        horizons_mock.assert_called_once()

        kwargs = horizons_mock.call_args.kwargs

        self.assertEqual(kwargs["id"], "3I")

        self.assertEqual(kwargs["location"], "500")

        self.assertAlmostEqual(kwargs["epochs"], Time("2025-12-07T09:01:30", format="isot", scale="utc").jd)

        miriade_mock.get_ephemerides.assert_not_called()

        _cached_known_interstellar_prediction.cache_clear()



    def test_detect_known_objects_projects_predictions_into_image_space(self) -> None:

        with tempfile.TemporaryDirectory() as temp_dir:

            root_path = Path(temp_dir)

            settings = AppSettings.from_root(root_path)

            source_path = root_path / "demo.fits"

            source_path.write_bytes(b"demo")

            solved_field = SolvedField(

                center_ra_deg=10.0,

                center_dec_deg=20.0,

                radius_deg=0.1,

                width=50,

                height=50,

                wcs_path=source_path,

            )

            wcs = WCS(naxis=2)

            wcs.wcs.crpix = [25.0, 25.0]

            wcs.wcs.cdelt = np.array([-1.0 / 3600.0, 1.0 / 3600.0])

            wcs.wcs.crval = [10.0, 20.0]

            wcs.wcs.ctype = ["RA---TAN", "DEC--TAN"]

            image_data = np.zeros((50, 50), dtype=float)

            image_data[24, 24] = 150.0

            query_service = _StubQueryService(

                [

                    SolarSystemPrediction(

                        name="(12345) Demo",

                        designation="12345",

                        object_type="Asteroid",

                        orbit_class="main-belt",

                        ra_deg=10.0,

                        dec_deg=20.0,

                        predicted_magnitude=14.2,

                        ra_rate_arcsec_per_hour=3.0,

                        dec_rate_arcsec_per_hour=4.0,

                        positional_uncertainty_arcsec=0.7,

                    )

                ]

            )



            with (

                patch("photometry_app.core.solar_system._resolve_source_field", return_value=(solved_field, False)),

                patch("photometry_app.core.solar_system._resolved_wcs_for_field", return_value=wcs),

                patch("photometry_app.core.solar_system.read_photometry_image_data", return_value=image_data),

            ):

                result = detect_known_solar_system_objects(

                    source_path,

                    observation_time=datetime(2025, 1, 14, 21, 12, tzinfo=UTC),

                    settings=settings,

                    exposure_seconds=120.0,

                    observer_latitude_deg=None,

                    observer_longitude_deg=None,

                    observer_elevation_m=None,

                    magnitude_limit=18.0,

                    query_service=query_service,

                )



        self.assertEqual(len(result.detections), 1)

        detection = result.detections[0]

        self.assertEqual(detection.name, "(12345) Demo")

        self.assertAlmostEqual(detection.predicted_x, 24.0, places=1)

        self.assertAlmostEqual(detection.predicted_y, 24.0, places=1)

        self.assertTrue(detection.likely_visible)

        self.assertGreater(detection.confidence_score, 0.5)

        self.assertIsNotNone(detection.local_flux)

    def test_detect_known_objects_selects_alternate_timestamp_with_better_image_match(self) -> None:

        with tempfile.TemporaryDirectory() as temp_dir:

            root_path = Path(temp_dir)

            settings = AppSettings.from_root(root_path)

            source_path = root_path / "demo.fits"

            source_path.write_bytes(b"demo")

            solved_field = SolvedField(

                center_ra_deg=10.0,

                center_dec_deg=20.0,

                radius_deg=0.1,

                width=50,

                height=50,

                wcs_path=source_path,

            )

            wcs = WCS(naxis=2)

            wcs.wcs.crpix = [25.0, 25.0]

            wcs.wcs.cdelt = np.array([-1.0 / 3600.0, 1.0 / 3600.0])

            wcs.wcs.crval = [10.0, 20.0]

            wcs.wcs.ctype = ["RA---TAN", "DEC--TAN"]

            image_data = np.zeros((50, 50), dtype=float)

            image_data[24, 24] = 150.0

            primary_time = datetime(2026, 5, 10, 8, 39, tzinfo=UTC)

            alternate_time = datetime(2026, 5, 10, 3, 39, tzinfo=UTC)

            good_ra, good_dec = wcs.pixel_to_world_values(24.0, 24.0)

            bad_ra, bad_dec = wcs.pixel_to_world_values(34.0, 34.0)



            class _TimeSwitchingQueryService:

                def __init__(self) -> None:

                    self.queried_times: list[datetime] = []

                def query_predictions(

                    self,

                    solved_field: SolvedField,

                    observation_time: datetime,

                    *,

                    observatory_code: str | None = None,

                    magnitude_limit: float | None = None,

                ) -> list[SolarSystemPrediction]:

                    del solved_field, observatory_code, magnitude_limit

                    self.queried_times.append(observation_time)

                    if abs((observation_time - (alternate_time + timedelta(seconds=60))).total_seconds()) < 1:

                        return [

                            SolarSystemPrediction(

                                name="(511) Davida",

                                designation="511",

                                object_type="Asteroid",

                                orbit_class="main-belt",

                                ra_deg=float(good_ra),

                                dec_deg=float(good_dec),

                                predicted_magnitude=13.0,

                                ra_rate_arcsec_per_hour=3.0,

                                dec_rate_arcsec_per_hour=0.0,

                            )

                        ]

                    return [

                        SolarSystemPrediction(

                            name="(511) Davida",

                            designation="511",

                            object_type="Asteroid",

                            orbit_class="main-belt",

                            ra_deg=float(bad_ra),

                            dec_deg=float(bad_dec),

                            predicted_magnitude=13.0,

                            ra_rate_arcsec_per_hour=3.0,

                            dec_rate_arcsec_per_hour=0.0,

                        )

                    ]

            query_service = _TimeSwitchingQueryService()

            with (

                patch("photometry_app.core.solar_system._resolve_source_field", return_value=(solved_field, False)),

                patch("photometry_app.core.solar_system._resolved_wcs_for_field", return_value=wcs),

                patch("photometry_app.core.solar_system.read_photometry_image_data", return_value=image_data),

            ):

                result = detect_known_solar_system_objects(

                    source_path,

                    observation_time=primary_time,

                    alternate_observation_times=(alternate_time,),

                    settings=settings,

                    exposure_seconds=120.0,

                    observer_latitude_deg=None,

                    observer_longitude_deg=None,

                    observer_elevation_m=None,

                    magnitude_limit=18.0,

                    query_service=query_service,

                )

        self.assertEqual(result.observation_time, alternate_time)

        self.assertEqual(len(query_service.queried_times), 2)

        self.assertAlmostEqual(result.detections[0].predicted_x, 24.0, places=1)

        self.assertTrue(result.detections[0].likely_visible)

        self.assertIn("alternate observation timestamp", result.summary_text)

        self.assertIn("captured", result.summary_text.lower())



    def test_measure_detection_in_frame_returns_current_frame_signal_metrics(self) -> None:

        with tempfile.TemporaryDirectory() as temp_dir:

            source_path = Path(temp_dir) / "demo.fits"

            source_path.write_bytes(b"demo")

            wcs = WCS(naxis=2)

            wcs.wcs.crpix = [25.0, 25.0]

            wcs.wcs.cdelt = np.array([-1.0 / 3600.0, 1.0 / 3600.0])

            wcs.wcs.crval = [10.0, 20.0]

            wcs.wcs.ctype = ["RA---TAN", "DEC--TAN"]

            image_data = np.zeros((50, 50), dtype=float)

            image_data[24, 24] = 200.0
            image_data[24, 23] = 120.0
            image_data[24, 25] = 120.0
            image_data[23, 24] = 120.0
            image_data[25, 24] = 120.0
            image_data[23, 23] = 60.0
            image_data[23, 25] = 60.0
            image_data[25, 23] = 60.0
            image_data[25, 25] = 60.0

            detection = SolarSystemDetection(

                name="(12345) Demo",

                designation="12345",

                object_type="Asteroid",

                orbit_class="main-belt",

                predicted_ra_deg=10.0,

                predicted_dec_deg=20.0,

                predicted_x=24.0,

                predicted_y=24.0,

                predicted_magnitude=14.2,

                ra_rate_arcsec_per_hour=3.0,

                dec_rate_arcsec_per_hour=4.0,

                motion_rate_arcsec_per_hour=5.0,

                expected_trail_length_px=0.2,

                positional_uncertainty_arcsec=0.7,

                altitude_deg=None,

                likely_visible=True,

                confidence_score=0.9,

                status="High-confidence match",

            )



            with (

                patch("photometry_app.core.solar_system.read_header", return_value=wcs.to_header()),

                patch("photometry_app.core.solar_system.read_photometry_image_data", return_value=image_data),

            ):

                measurement = measure_detection_in_frame(

                    source_path,

                    detection,

                    reference_observation_time=datetime(2025, 1, 14, 21, 13, tzinfo=UTC),

                    observation_time=datetime(2025, 1, 14, 21, 13, tzinfo=UTC),

                    exposure_seconds=120.0,

                )



        self.assertIsNotNone(measurement)

        assert measurement is not None

        self.assertIsNotNone(measurement.local_snr)

        self.assertIsNotNone(measurement.local_flux)

        self.assertIsNotNone(measurement.local_fwhm_px)

        self.assertIsNotNone(measurement.measured_ra_deg)

        self.assertIsNotNone(measurement.measured_dec_deg)

        expected_measured_ra_deg, expected_measured_dec_deg = wcs.pixel_to_world_values(measurement.measured_x, measurement.measured_y)

        self.assertAlmostEqual(measurement.measured_ra_deg or 0.0, float(expected_measured_ra_deg), places=8)

        self.assertAlmostEqual(measurement.measured_dec_deg or 0.0, float(expected_measured_dec_deg), places=8)

        self.assertTrue(measurement.likely_visible)



    def test_measure_detections_in_frame_reads_frame_once_for_multiple_detections(self) -> None:

        with tempfile.TemporaryDirectory() as temp_dir:

            source_path = Path(temp_dir) / "demo.fits"

            source_path.write_bytes(b"demo")

            wcs = WCS(naxis=2)

            wcs.wcs.crpix = [25.0, 25.0]

            wcs.wcs.cdelt = np.array([-1.0 / 3600.0, 1.0 / 3600.0])

            wcs.wcs.crval = [10.0, 20.0]

            wcs.wcs.ctype = ["RA---TAN", "DEC--TAN"]

            image_data = np.zeros((50, 50), dtype=float)

            image_data[24, 24] = 200.0

            detections = [

                SolarSystemDetection(

                    name="(12345) Demo A",

                    designation="12345",

                    object_type="Asteroid",

                    orbit_class="main-belt",

                    predicted_ra_deg=10.0,

                    predicted_dec_deg=20.0,

                    predicted_x=24.0,

                    predicted_y=24.0,

                    predicted_magnitude=14.2,

                    ra_rate_arcsec_per_hour=3.0,

                    dec_rate_arcsec_per_hour=4.0,

                    motion_rate_arcsec_per_hour=5.0,

                    expected_trail_length_px=0.2,

                    positional_uncertainty_arcsec=0.7,

                    altitude_deg=None,

                    likely_visible=True,

                    confidence_score=0.9,

                    status="High-confidence match",

                ),

                SolarSystemDetection(

                    name="(12346) Demo B",

                    designation="12346",

                    object_type="Asteroid",

                    orbit_class="main-belt",

                    predicted_ra_deg=10.0,

                    predicted_dec_deg=20.0,

                    predicted_x=24.0,

                    predicted_y=24.0,

                    predicted_magnitude=14.4,

                    ra_rate_arcsec_per_hour=2.0,

                    dec_rate_arcsec_per_hour=3.0,

                    motion_rate_arcsec_per_hour=3.6,

                    expected_trail_length_px=0.2,

                    positional_uncertainty_arcsec=0.7,

                    altitude_deg=None,

                    likely_visible=True,

                    confidence_score=0.85,

                    status="High-confidence match",

                ),

            ]



            with (

                patch("photometry_app.core.solar_system.read_header", return_value=wcs.to_header()) as read_header_mock,

                patch("photometry_app.core.solar_system.read_photometry_image_data", return_value=image_data) as read_image_mock,

            ):

                measurements = measure_detections_in_frame(

                    source_path,

                    detections,

                    reference_observation_time=datetime(2025, 1, 14, 21, 13, tzinfo=UTC),

                    observation_time=datetime(2025, 1, 14, 21, 13, tzinfo=UTC),

                    exposure_seconds=120.0,

                )



        self.assertEqual(len(measurements), 2)

        self.assertEqual(read_header_mock.call_count, 1)

        self.assertEqual(read_image_mock.call_count, 1)

        self.assertTrue(all(measurement is not None for measurement in measurements))



    def test_measure_detections_in_frame_can_skip_local_matching_and_keep_predictions(self) -> None:

        with tempfile.TemporaryDirectory() as temp_dir:

            source_path = Path(temp_dir) / "demo.fits"

            source_path.write_bytes(b"demo")

            wcs = WCS(naxis=2)

            wcs.wcs.crpix = [25.0, 25.0]

            wcs.wcs.cdelt = np.array([-1.0 / 3600.0, 1.0 / 3600.0])

            wcs.wcs.crval = [10.0, 20.0]

            wcs.wcs.ctype = ["RA---TAN", "DEC--TAN"]

            detections = [

                SolarSystemDetection(

                    name="(12345) Demo A",

                    designation="12345",

                    object_type="Asteroid",

                    orbit_class="main-belt",

                    predicted_ra_deg=10.0,

                    predicted_dec_deg=20.0,

                    predicted_x=24.0,

                    predicted_y=24.0,

                    predicted_magnitude=14.2,

                    ra_rate_arcsec_per_hour=3.0,

                    dec_rate_arcsec_per_hour=4.0,

                    motion_rate_arcsec_per_hour=5.0,

                    expected_trail_length_px=0.2,

                    positional_uncertainty_arcsec=0.7,

                    altitude_deg=None,

                    likely_visible=True,

                    confidence_score=0.9,

                    status="High-confidence match",

                )

            ]



            with (

                patch("photometry_app.core.solar_system.read_header", return_value=wcs.to_header()) as read_header_mock,

                patch("photometry_app.core.solar_system.read_photometry_image_data") as read_image_mock,

            ):

                measurements = measure_detections_in_frame(

                    source_path,

                    detections,

                    reference_observation_time=datetime(2025, 1, 14, 21, 13, tzinfo=UTC),

                    observation_time=datetime(2025, 1, 14, 21, 18, tzinfo=UTC),

                    exposure_seconds=120.0,

                    measure_local_match=False,

                )



        self.assertEqual(len(measurements), 1)

        self.assertEqual(read_header_mock.call_count, 1)

        read_image_mock.assert_not_called()

        self.assertIsNotNone(measurements[0])

        assert measurements[0] is not None

        self.assertIsNone(measurements[0].measured_x)

        self.assertIsNone(measurements[0].measured_y)

        self.assertIsNone(measurements[0].local_snr)

    def test_measure_detections_in_frame_can_use_fallback_solved_field_for_aligned_frames(self) -> None:

        with tempfile.TemporaryDirectory() as temp_dir:

            source_path = Path(temp_dir) / "aligned_frame.fits"

            wcs_path = Path(temp_dir) / "reference_frame.fits"

            source_path.write_bytes(b"aligned")

            wcs_path.write_bytes(b"reference")

            fallback_wcs = WCS(naxis=2)

            fallback_wcs.wcs.crpix = [25.0, 25.0]

            fallback_wcs.wcs.cdelt = np.array([-1.0 / 3600.0, 1.0 / 3600.0])

            fallback_wcs.wcs.crval = [10.0, 20.0]

            fallback_wcs.wcs.ctype = ["RA---TAN", "DEC--TAN"]

            detection = SolarSystemDetection(

                name="(12345) Demo A",

                designation="12345",

                object_type="Asteroid",

                orbit_class="main-belt",

                predicted_ra_deg=10.0,

                predicted_dec_deg=20.0,

                predicted_x=4.0,

                predicted_y=7.0,

                predicted_magnitude=14.2,

                ra_rate_arcsec_per_hour=3.0,

                dec_rate_arcsec_per_hour=4.0,

                motion_rate_arcsec_per_hour=5.0,

                expected_trail_length_px=0.2,

                positional_uncertainty_arcsec=0.7,

                altitude_deg=None,

                likely_visible=True,

                confidence_score=0.9,

                status="High-confidence match",

            )

            solved_field = SolvedField(

                center_ra_deg=10.0,

                center_dec_deg=20.0,

                radius_deg=1.0,

                width=50,

                height=50,

                wcs_path=wcs_path,

            )

            with (

                patch("photometry_app.core.solar_system.read_header", return_value={}),

                patch("photometry_app.core.solar_system._resolved_wcs_for_field", return_value=fallback_wcs),

                patch("photometry_app.core.solar_system.read_photometry_image_data") as read_image_mock,

            ):

                measurements = measure_detections_in_frame(

                    source_path,

                    [detection],

                    reference_observation_time=datetime(2025, 1, 14, 21, 13, tzinfo=UTC),

                    observation_time=datetime(2025, 1, 14, 21, 13, tzinfo=UTC),

                    exposure_seconds=120.0,

                    measure_local_match=False,

                    fallback_solved_field=solved_field,

                )

        self.assertEqual(len(measurements), 1)

        self.assertIsNotNone(measurements[0])

        assert measurements[0] is not None

        read_image_mock.assert_not_called()

        self.assertAlmostEqual(measurements[0].predicted_x, 24.0, places=6)

        self.assertAlmostEqual(measurements[0].predicted_y, 24.0, places=6)

        self.assertIsNone(measurements[0].local_fwhm_px)

        self.assertTrue(np.isfinite(measurements[0].predicted_x))

        self.assertTrue(np.isfinite(measurements[0].predicted_y))



    def test_measure_detections_in_frame_keeps_other_predictions_when_one_local_match_fails(self) -> None:

        with tempfile.TemporaryDirectory() as temp_dir:

            source_path = Path(temp_dir) / "demo.fits"

            source_path.write_bytes(b"demo")

            wcs = WCS(naxis=2)

            wcs.wcs.crpix = [25.0, 25.0]

            wcs.wcs.cdelt = np.array([-1.0 / 3600.0, 1.0 / 3600.0])

            wcs.wcs.crval = [10.0, 20.0]

            wcs.wcs.ctype = ["RA---TAN", "DEC--TAN"]

            image_data = np.zeros((50, 50), dtype=float)

            detections = [

                SolarSystemDetection(

                    name="(12345) Demo A",

                    designation="12345",

                    object_type="Asteroid",

                    orbit_class="main-belt",

                    predicted_ra_deg=10.0,

                    predicted_dec_deg=20.0,

                    predicted_x=24.0,

                    predicted_y=24.0,

                    predicted_magnitude=14.2,

                    ra_rate_arcsec_per_hour=3.0,

                    dec_rate_arcsec_per_hour=4.0,

                    motion_rate_arcsec_per_hour=5.0,

                    expected_trail_length_px=0.2,

                    positional_uncertainty_arcsec=0.7,

                    altitude_deg=None,

                    likely_visible=True,

                    confidence_score=0.9,

                    status="High-confidence match",

                ),

                SolarSystemDetection(

                    name="(12346) Demo B",

                    designation="12346",

                    object_type="Asteroid",

                    orbit_class="main-belt",

                    predicted_ra_deg=10.0,

                    predicted_dec_deg=20.0,

                    predicted_x=24.0,

                    predicted_y=24.0,

                    predicted_magnitude=14.4,

                    ra_rate_arcsec_per_hour=2.0,

                    dec_rate_arcsec_per_hour=3.0,

                    motion_rate_arcsec_per_hour=3.6,

                    expected_trail_length_px=0.2,

                    positional_uncertainty_arcsec=0.7,

                    altitude_deg=None,

                    likely_visible=True,

                    confidence_score=0.85,

                    status="High-confidence match",

                ),

            ]

            observation_time = datetime(2025, 1, 14, 21, 13, tzinfo=UTC)

            call_log: list[tuple[str, bool]] = []

            def fake_measure(

                source_path_arg,

                detection,

                *,

                wcs,

                image_data,

                pixel_scale_arcsec_per_pixel,

                reference_observation_time,

                observation_time,

                exposure_seconds,

                measure_local_match,

            ):

                call_log.append((detection.name, measure_local_match))

                if detection.designation == "12345" and measure_local_match:

                    raise RuntimeError("local-match failure")

                predicted_x = 11.0 if detection.designation == "12345" else 22.0

                predicted_y = 12.0 if detection.designation == "12345" else 23.0

                return SolarSystemFrameMeasurement(

                    source_path=source_path_arg,

                    observation_time=observation_time,

                    predicted_ra_deg=detection.predicted_ra_deg,

                    predicted_dec_deg=detection.predicted_dec_deg,

                    predicted_x=predicted_x,

                    predicted_y=predicted_y,

                    expected_trail_length_px=detection.expected_trail_length_px,

                )



            with (

                patch("photometry_app.core.solar_system.read_header", return_value=wcs.to_header()),

                patch("photometry_app.core.solar_system.read_photometry_image_data", return_value=image_data),

                patch("photometry_app.core.solar_system._measure_detection_in_frame_data", side_effect=fake_measure),

            ):

                measurements = measure_detections_in_frame(

                    source_path,

                    detections,

                    reference_observation_time=observation_time,

                    observation_time=observation_time,

                    exposure_seconds=120.0,

                )



        self.assertEqual(call_log, [

            ("(12345) Demo A", True),

            ("(12345) Demo A", False),

            ("(12346) Demo B", True),

        ])

        self.assertEqual(len(measurements), 2)

        self.assertIsNotNone(measurements[0])

        self.assertIsNotNone(measurements[1])

        assert measurements[0] is not None

        assert measurements[1] is not None

        self.assertAlmostEqual(measurements[0].predicted_x, 11.0)

        self.assertAlmostEqual(measurements[0].predicted_y, 12.0)

        self.assertAlmostEqual(measurements[1].predicted_x, 22.0)

        self.assertAlmostEqual(measurements[1].predicted_y, 23.0)



    def test_build_known_object_heliocentric_context_uses_smallbody_designation_for_object_vectors(self) -> None:

        detection = SolarSystemDetection(

            name="(1) Ceres",

            designation="1",

            object_type="Asteroid",

            orbit_class="main-belt",

            predicted_ra_deg=10.0,

            predicted_dec_deg=20.0,

            predicted_x=24.0,

            predicted_y=24.0,

            predicted_magnitude=12.3,

            ra_rate_arcsec_per_hour=3.0,

            dec_rate_arcsec_per_hour=1.0,

            motion_rate_arcsec_per_hour=3.2,

            expected_trail_length_px=0.5,

            positional_uncertainty_arcsec=0.2,

            altitude_deg=45.0,

            likely_visible=True,

            confidence_score=0.9,

            status="Likely visible",

        )

        observation_times = (

            datetime(2025, 1, 14, 21, 13, tzinfo=UTC),

            datetime(2025, 1, 14, 21, 15, tzinfo=UTC),

        )

        captured_queries: list[dict[str, object]] = []



        class _FakeHorizons:

            def __init__(self, **kwargs: object) -> None:

                self._kwargs = dict(kwargs)

                captured_queries.append(self._kwargs)



            def vectors(self) -> Table:

                epochs = self._kwargs["epochs"]

                epoch_count = len(epochs) if isinstance(epochs, list) else 1

                target_id = str(self._kwargs["id"])

                target_name = "1 Ceres (A801 AA)" if target_id == "1" else "Earth (399)"

                base_value = 2.0 if target_id == "1" else 1.0

                rows = [

                    (

                        target_name,

                        base_value + (index * 0.1),

                        base_value + (index * 0.2),

                        base_value + (index * 0.05),

                        0.01,

                        0.02,

                        0.03,

                    )

                    for index in range(epoch_count)

                ]

                return Table(rows=rows, names=("targetname", "x", "y", "z", "vx", "vy", "vz"))



        with patch("photometry_app.core.solar_system.Horizons", _FakeHorizons):

            context = build_known_object_heliocentric_context(

                detection,

                observation_times=observation_times,

                arc_padding_days=1.0,

                sample_count=5,

            )



        self.assertEqual(context.object_label, "(1) Ceres")

        self.assertEqual(context.resolved_target_name, "1 Ceres (A801 AA)")

        self.assertEqual(context.earth_target_name, "Earth (399)")

        self.assertEqual(context.arc_padding_days, 1.0)

        self.assertEqual(len(context.object_path_samples), 5)

        self.assertEqual(len(context.observation_object_samples), 2)

        self.assertEqual(captured_queries[0]["id"], "1")

        self.assertEqual(captured_queries[0]["id_type"], "smallbody")

        self.assertEqual(captured_queries[0]["location"], "500@10")

        self.assertEqual(captured_queries[1]["id"], "1")

        earth_queries = [query for query in captured_queries if query.get("id") == "399"]

        self.assertEqual(len(earth_queries), 2)

        self.assertNotIn("id_type", earth_queries[0])



    def test_build_known_object_heliocentric_context_uses_exact_comet_designation_for_ambiguous_periodic_comets(self) -> None:

        detection = SolarSystemDetection(

            name="154P",

            designation="154P",

            object_type="Comet",

            orbit_class="comet",

            predicted_ra_deg=10.0,

            predicted_dec_deg=20.0,

            predicted_x=24.0,

            predicted_y=24.0,

            predicted_magnitude=12.3,

            ra_rate_arcsec_per_hour=3.0,

            dec_rate_arcsec_per_hour=1.0,

            motion_rate_arcsec_per_hour=3.2,

            expected_trail_length_px=0.5,

            positional_uncertainty_arcsec=0.2,

            altitude_deg=45.0,

            likely_visible=True,

            confidence_score=0.9,

            status="Likely visible",

        )

        observation_times = (

            datetime(2025, 1, 14, 21, 13, tzinfo=UTC),

            datetime(2025, 1, 14, 21, 15, tzinfo=UTC),

        )

        captured_queries: list[dict[str, object]] = []



        class _FakeHorizons:

            def __init__(self, **kwargs: object) -> None:

                self._kwargs = dict(kwargs)

                captured_queries.append(self._kwargs)



            def vectors(self) -> Table:

                target_id = str(self._kwargs["id"])

                if target_id == "154P":

                    raise ValueError("Ambiguous target name; provide unique id:")

                epochs = self._kwargs["epochs"]

                epoch_count = len(epochs) if isinstance(epochs, list) else 1

                target_name = "154P/Brewington" if target_id == "DES=154P;CAP" else "Earth (399)"

                base_value = 2.0 if target_id == "DES=154P;CAP" else 1.0

                rows = [

                    (

                        target_name,

                        base_value + (index * 0.1),

                        base_value + (index * 0.2),

                        base_value + (index * 0.05),

                        0.01,

                        0.02,

                        0.03,

                    )

                    for index in range(epoch_count)

                ]

                return Table(rows=rows, names=("targetname", "x", "y", "z", "vx", "vy", "vz"))



        with patch("photometry_app.core.solar_system.Horizons", _FakeHorizons):

            context = build_known_object_heliocentric_context(

                detection,

                observation_times=observation_times,

                arc_padding_days=1.0,

                sample_count=5,

            )



        self.assertEqual(context.resolved_target_name, "154P/Brewington")

        self.assertEqual(captured_queries[0]["id"], "DES=154P;CAP")

        self.assertNotIn("id_type", captured_queries[0])



    def test_build_known_object_heliocentric_context_chunks_large_epoch_lists(self) -> None:

        detection = SolarSystemDetection(

            name="3I/ATLAS",

            designation="3I",

            object_type="Comet",

            orbit_class="interstellar",

            predicted_ra_deg=10.0,

            predicted_dec_deg=20.0,

            predicted_x=24.0,

            predicted_y=24.0,

            predicted_magnitude=15.1,

            ra_rate_arcsec_per_hour=3.0,

            dec_rate_arcsec_per_hour=1.0,

            motion_rate_arcsec_per_hour=3.2,

            expected_trail_length_px=0.5,

            positional_uncertainty_arcsec=0.2,

            altitude_deg=45.0,

            likely_visible=True,

            confidence_score=0.9,

            status="Likely visible",

        )

        observation_times = tuple(

            datetime(2025, 12, 7, 9, 1, tzinfo=UTC) + timedelta(minutes=index)

            for index in range(48)

        )

        captured_queries: list[dict[str, object]] = []



        class _FakeHorizons:

            def __init__(self, **kwargs: object) -> None:

                self._kwargs = dict(kwargs)

                captured_queries.append(self._kwargs)



            def vectors(self) -> Table:

                epochs = self._kwargs["epochs"]

                epoch_count = len(epochs) if isinstance(epochs, list) else 1

                target_id = str(self._kwargs["id"])

                target_name = "ATLAS (C/2025 N1)" if target_id == "3I" else "Earth (399)"

                rows = [

                    (

                        target_name,

                        1.0 + (index * 0.01),

                        2.0 + (index * 0.01),

                        3.0 + (index * 0.01),

                        0.01,

                        0.02,

                        0.03,

                    )

                    for index in range(epoch_count)

                ]

                return Table(rows=rows, names=("targetname", "x", "y", "z", "vx", "vy", "vz"))



        with patch("photometry_app.core.solar_system.Horizons", _FakeHorizons):

            context = build_known_object_heliocentric_context(

                detection,

                observation_times=observation_times,

                arc_padding_days=45.0,

                sample_count=61,

            )



        self.assertEqual(len(context.object_path_samples), 61)

        self.assertEqual(len(context.observation_object_samples), 48)

        self.assertEqual(context.arc_padding_days, 45.0)

        self.assertTrue(all(len(query["epochs"]) <= 32 for query in captured_queries if isinstance(query.get("epochs"), list)))

        self.assertGreaterEqual(len(captured_queries), 8)



    def test_build_known_object_heliocentric_context_can_include_major_planets(self) -> None:

        detection = SolarSystemDetection(

            name="(20) Massalia",

            designation="20",

            object_type="Asteroid",

            orbit_class="main-belt",

            predicted_ra_deg=10.0,

            predicted_dec_deg=20.0,

            predicted_x=24.0,

            predicted_y=24.0,

            predicted_magnitude=8.4,

            ra_rate_arcsec_per_hour=3.0,

            dec_rate_arcsec_per_hour=1.0,

            motion_rate_arcsec_per_hour=3.2,

            expected_trail_length_px=0.5,

            positional_uncertainty_arcsec=0.2,

            altitude_deg=45.0,

            likely_visible=True,

            confidence_score=0.9,

            status="Likely visible",

        )

        observation_times = (

            datetime(2025, 12, 7, 9, 13, tzinfo=UTC),

            datetime(2025, 12, 7, 9, 15, tzinfo=UTC),

        )

        captured_queries: list[dict[str, object]] = []

        target_axes = {

            "20": ("20 Massalia (A852 SA)", 2.41),

            "399": ("Earth (399)", 1.0),

            "199": ("Mercury (199)", 0.387),

            "299": ("Venus (299)", 0.723),

            "499": ("Mars (499)", 1.524),

            "599": ("Jupiter (599)", 5.203),

            "699": ("Saturn (699)", 9.537),

            "799": ("Uranus (799)", 19.191),

            "899": ("Neptune (899)", 30.07),

        }

        sun_mu = 0.00029591220828559115



        class _FakeHorizons:

            def __init__(self, **kwargs: object) -> None:

                self._kwargs = dict(kwargs)

                captured_queries.append(self._kwargs)



            def vectors(self) -> Table:

                epochs = self._kwargs["epochs"]

                epoch_count = len(epochs) if isinstance(epochs, list) else 1

                target_id = str(self._kwargs["id"])

                target_name, semi_major_axis = target_axes[target_id]

                mean_motion = math.sqrt(sun_mu / (semi_major_axis ** 3))

                rows = []

                for index in range(epoch_count):

                    theta = 0.04 * index

                    x_value = semi_major_axis * math.cos(theta)

                    y_value = semi_major_axis * math.sin(theta)

                    vx_value = -semi_major_axis * mean_motion * math.sin(theta)

                    vy_value = semi_major_axis * mean_motion * math.cos(theta)

                    rows.append((target_name, x_value, y_value, 0.0, vx_value, vy_value, 0.0))

                return Table(rows=rows, names=("targetname", "x", "y", "z", "vx", "vy", "vz"))



        with patch("photometry_app.core.solar_system.Horizons", _FakeHorizons):

            context = build_known_object_heliocentric_context(

                detection,

                observation_times=observation_times,

                arc_padding_days=10.0,

                sample_count=7,

                include_major_planets=True,

            )



        self.assertTrue(context.include_major_planets)

        self.assertEqual(len(context.additional_bodies), 7)

        self.assertEqual([body.label for body in context.additional_bodies], ["Mercury", "Venus", "Mars", "Jupiter", "Saturn", "Uranus", "Neptune"])

        self.assertIsNotNone(context.object_orbital_period_days)

        self.assertIsNotNone(context.earth_orbital_period_days)

        self.assertAlmostEqual(context.earth_orbital_period_days or 0.0, 365.25, delta=5.0)

        queried_ids = {str(query["id"]) for query in captured_queries}

        self.assertTrue({"199", "299", "499", "599", "699", "799", "899"}.issubset(queried_ids))

    def test_query_major_planet_heliocentric_paths_reuses_persisted_cache(self) -> None:

        query_times = (

            datetime(2026, 4, 26, 0, 0, tzinfo=UTC),

            datetime(2026, 4, 27, 0, 0, tzinfo=UTC),

        )

        cached_samples = (

            SolarSystemStateVectorSample(

                observation_time=query_times[0],

                x_au=0.4,

                y_au=0.1,

                z_au=0.0,

                vx_au_per_day=0.0,

                vy_au_per_day=0.0,

                vz_au_per_day=0.0,

            ),

            SolarSystemStateVectorSample(

                observation_time=query_times[1],

                x_au=0.5,

                y_au=0.2,

                z_au=0.0,

                vx_au_per_day=0.0,

                vy_au_per_day=0.0,

                vz_au_per_day=0.0,

            ),

        )



        with tempfile.TemporaryDirectory() as temp_dir:

            cache_dir = Path(temp_dir)

            with (

                patch("photometry_app.core.solar_system._major_planet_cache_directory", return_value=cache_dir),

                patch("photometry_app.core.solar_system._MAJOR_PLANET_TARGETS", (("mercury", "Mercury", "199"),)),

                patch("photometry_app.core.solar_system._query_horizons_vector_samples", return_value=("Mercury (199)", cached_samples)) as query_vectors,

            ):

                first_result = _query_major_planet_heliocentric_paths(query_times)



            self.assertEqual(query_vectors.call_count, 1)

            self.assertEqual(first_result[0].label, "Mercury")

            self.assertEqual(first_result[0].path_samples, cached_samples)



            with (

                patch("photometry_app.core.solar_system._major_planet_cache_directory", return_value=cache_dir),

                patch("photometry_app.core.solar_system._MAJOR_PLANET_TARGETS", (("mercury", "Mercury", "199"),)),

                patch(

                    "photometry_app.core.solar_system._query_horizons_vector_samples",

                    side_effect=AssertionError("Major-planet cache should avoid a second live Horizons query."),

                ),

            ):

                second_result = _query_major_planet_heliocentric_paths(query_times)



        self.assertEqual(second_result[0].resolved_target_name, "Mercury (199)")

        self.assertEqual(second_result[0].path_samples, cached_samples)



    def test_build_multi_known_object_heliocentric_context_adds_selected_comparison_tracks(self) -> None:

        first_detection = SolarSystemDetection(

            name="(1) Ceres",

            designation="1",

            object_type="Asteroid",

            orbit_class="main-belt",

            predicted_ra_deg=10.0,

            predicted_dec_deg=20.0,

            predicted_x=24.0,

            predicted_y=24.0,

            predicted_magnitude=12.3,

            ra_rate_arcsec_per_hour=3.0,

            dec_rate_arcsec_per_hour=1.0,

            motion_rate_arcsec_per_hour=3.2,

            expected_trail_length_px=0.5,

            positional_uncertainty_arcsec=0.2,

            altitude_deg=45.0,

            likely_visible=True,

            confidence_score=0.9,

            status="Likely visible",

        )

        second_detection = SolarSystemDetection(

            name="(20) Massalia",

            designation="20",

            object_type="Asteroid",

            orbit_class="main-belt",

            predicted_ra_deg=11.0,

            predicted_dec_deg=21.0,

            predicted_x=28.0,

            predicted_y=27.0,

            predicted_magnitude=8.4,

            ra_rate_arcsec_per_hour=2.4,

            dec_rate_arcsec_per_hour=0.3,

            motion_rate_arcsec_per_hour=2.4,

            expected_trail_length_px=0.5,

            positional_uncertainty_arcsec=0.2,

            altitude_deg=46.0,

            likely_visible=True,

            confidence_score=0.88,

            status="Likely visible",

        )

        observation_times = (

            datetime(2025, 12, 7, 9, 13, tzinfo=UTC),

            datetime(2025, 12, 7, 9, 15, tzinfo=UTC),

        )

        target_axes = {

            "1": ("1 Ceres (A801 AA)", 2.77),

            "20": ("20 Massalia (A852 SA)", 2.41),

            "399": ("Earth (399)", 1.0),

        }

        sun_mu = 0.00029591220828559115



        class _FakeHorizons:

            def __init__(self, **kwargs: object) -> None:

                self._kwargs = dict(kwargs)



            def vectors(self) -> Table:

                epochs = self._kwargs["epochs"]

                epoch_count = len(epochs) if isinstance(epochs, list) else 1

                target_id = str(self._kwargs["id"])

                target_name, semi_major_axis = target_axes[target_id]

                mean_motion = math.sqrt(sun_mu / (semi_major_axis ** 3))

                rows = []

                for index in range(epoch_count):

                    theta = 0.05 * index

                    x_value = semi_major_axis * math.cos(theta)

                    y_value = semi_major_axis * math.sin(theta)

                    vx_value = -semi_major_axis * mean_motion * math.sin(theta)

                    vy_value = semi_major_axis * mean_motion * math.cos(theta)

                    rows.append((target_name, x_value, y_value, 0.0, vx_value, vy_value, 0.0))

                return Table(rows=rows, names=("targetname", "x", "y", "z", "vx", "vy", "vz"))



            def ephemerides(self) -> Table:

                epochs = self._kwargs["epochs"]

                epoch_count = len(epochs) if isinstance(epochs, list) else 1

                target_id = str(self._kwargs["id"])

                target_name, _semi_major_axis = target_axes[target_id]

                rows = []

                for index in range(epoch_count):

                    rows.append((target_name, 10.5 + (0.08 * index)))

                return Table(rows=rows, names=("targetname", "V"))



        with patch("photometry_app.core.solar_system.Horizons", _FakeHorizons):

            context = build_multi_known_object_heliocentric_context(

                (first_detection, second_detection),

                observation_times=observation_times,

                arc_padding_days=10.0,

                sample_count=7,

            )



        self.assertEqual(context.object_label, "(1) Ceres")

        self.assertEqual(context.resolved_target_name, "1 Ceres (A801 AA)")

        self.assertEqual(len(context.comparison_tracks), 1)

        self.assertEqual(context.comparison_tracks[0].object_label, "(20) Massalia")

        self.assertEqual(context.comparison_tracks[0].resolved_target_name, "20 Massalia (A852 SA)")

        self.assertEqual(len(context.comparison_tracks[0].path_samples), 7)

        self.assertEqual(len(context.comparison_tracks[0].observation_samples), 2)

        self.assertEqual(len(context.object_magnitude_samples), 7)

        self.assertEqual(len(context.comparison_tracks[0].magnitude_samples), 7)



    def test_estimate_visible_magnitude_limit_uses_dimmest_visible_bin(self) -> None:

        with tempfile.TemporaryDirectory() as temp_dir:

            root_path = Path(temp_dir)

            settings = AppSettings.from_root(root_path)

            source_path = root_path / "demo.fits"

            source_path.write_bytes(b"demo")

            solved_field = SolvedField(

                center_ra_deg=10.0,

                center_dec_deg=20.0,

                radius_deg=0.1,

                width=60,

                height=60,

                wcs_path=source_path,

            )

            wcs = WCS(naxis=2)

            wcs.wcs.crpix = [25.0, 25.0]

            wcs.wcs.cdelt = np.array([-1.0 / 3600.0, 1.0 / 3600.0])

            wcs.wcs.crval = [10.0, 20.0]

            wcs.wcs.ctype = ["RA---TAN", "DEC--TAN"]

            image_data = np.zeros((60, 60), dtype=float)

            stars = [

                CatalogStar(

                    catalog="gaia-dr3",

                    source_id="v1",

                    name="V1",

                    ra_deg=float(wcs.pixel_to_world_values(24.0, 24.0)[0]),

                    dec_deg=float(wcs.pixel_to_world_values(24.0, 24.0)[1]),

                    magnitude=16.1,

                    is_variable=False,

                ),

                CatalogStar(

                    catalog="gaia-dr3",

                    source_id="v2",

                    name="V2",

                    ra_deg=float(wcs.pixel_to_world_values(26.0, 26.0)[0]),

                    dec_deg=float(wcs.pixel_to_world_values(26.0, 26.0)[1]),

                    magnitude=16.2,

                    is_variable=False,

                ),

                CatalogStar(

                    catalog="gaia-dr3",

                    source_id="v3",

                    name="V3",

                    ra_deg=float(wcs.pixel_to_world_values(28.0, 28.0)[0]),

                    dec_deg=float(wcs.pixel_to_world_values(28.0, 28.0)[1]),

                    magnitude=16.4,

                    is_variable=False,

                ),

                CatalogStar(

                    catalog="gaia-dr3",

                    source_id="d1",

                    name="D1",

                    ra_deg=float(wcs.pixel_to_world_values(30.0, 30.0)[0]),

                    dec_deg=float(wcs.pixel_to_world_values(30.0, 30.0)[1]),

                    magnitude=18.6,

                    is_variable=False,

                ),

                CatalogStar(

                    catalog="gaia-dr3",

                    source_id="d2",

                    name="D2",

                    ra_deg=float(wcs.pixel_to_world_values(32.0, 32.0)[0]),

                    dec_deg=float(wcs.pixel_to_world_values(32.0, 32.0)[1]),

                    magnitude=18.7,

                    is_variable=False,

                ),

                CatalogStar(

                    catalog="gaia-dr3",

                    source_id="d3",

                    name="D3",

                    ra_deg=float(wcs.pixel_to_world_values(34.0, 34.0)[0]),

                    dec_deg=float(wcs.pixel_to_world_values(34.0, 34.0)[1]),

                    magnitude=18.8,

                    is_variable=False,

                ),

            ]

            catalog_service = _StubGaiaCatalogService(stars)



            with (

                patch("photometry_app.core.solar_system._resolve_source_field", return_value=(solved_field, False)),

                patch("photometry_app.core.solar_system._resolved_wcs_for_field", return_value=wcs),

                patch("photometry_app.core.solar_system.read_photometry_image_data", return_value=image_data),

                patch(

                    "photometry_app.core.solar_system._measure_local_match",

                    side_effect=lambda _image_data, predicted_x, predicted_y, expected_trail_length_px=None: (

                        predicted_x,

                        predicted_y,

                        1.0,

                        8.0 if predicted_x < 30.0 else 5.2,

                        200.0,

                        1000.0,

                    ),

                ),

            ):

                result = estimate_visible_magnitude_limit(

                    source_path,

                    settings=settings,

                    catalog_service=catalog_service,

                )



        self.assertAlmostEqual(result.dimmest_visible_magnitude, 18.8)

        self.assertEqual(result.visible_star_count, 3)

        self.assertEqual(result.tested_star_count, 6)

        self.assertIn("18.8", result.summary_text)



    def test_estimate_visible_magnitude_limit_reports_detailed_progress_messages(self) -> None:

        with tempfile.TemporaryDirectory() as temp_dir:

            root_path = Path(temp_dir)

            settings = AppSettings.from_root(root_path)

            source_path = root_path / "demo.fits"

            source_path.write_bytes(b"demo")

            solved_field = SolvedField(

                center_ra_deg=10.0,

                center_dec_deg=20.0,

                radius_deg=0.1,

                width=60,

                height=60,

                wcs_path=source_path,

            )

            wcs = WCS(naxis=2)

            wcs.wcs.crpix = [25.0, 25.0]

            wcs.wcs.cdelt = np.array([-1.0 / 3600.0, 1.0 / 3600.0])

            wcs.wcs.crval = [10.0, 20.0]

            wcs.wcs.ctype = ["RA---TAN", "DEC--TAN"]

            stars = [

                CatalogStar(

                    catalog="gaia-dr3",

                    source_id="d1",

                    name="D1",

                    ra_deg=float(wcs.pixel_to_world_values(30.0, 30.0)[0]),

                    dec_deg=float(wcs.pixel_to_world_values(30.0, 30.0)[1]),

                    magnitude=18.6,

                    is_variable=False,

                ),

                CatalogStar(

                    catalog="gaia-dr3",

                    source_id="d2",

                    name="D2",

                    ra_deg=float(wcs.pixel_to_world_values(32.0, 32.0)[0]),

                    dec_deg=float(wcs.pixel_to_world_values(32.0, 32.0)[1]),

                    magnitude=18.7,

                    is_variable=False,

                ),

                CatalogStar(

                    catalog="gaia-dr3",

                    source_id="d3",

                    name="D3",

                    ra_deg=float(wcs.pixel_to_world_values(34.0, 34.0)[0]),

                    dec_deg=float(wcs.pixel_to_world_values(34.0, 34.0)[1]),

                    magnitude=18.8,

                    is_variable=False,

                ),

            ]

            catalog_service = _StubGaiaCatalogService(stars)

            progress_messages: list[str] = []



            with (

                patch("photometry_app.core.solar_system._resolve_source_field", return_value=(solved_field, False)),

                patch("photometry_app.core.solar_system._resolved_wcs_for_field", return_value=wcs),

                patch("photometry_app.core.solar_system.read_photometry_image_data", return_value=np.zeros((60, 60), dtype=float)),

                patch(

                    "photometry_app.core.solar_system._measure_local_match",

                    side_effect=lambda _image_data, predicted_x, predicted_y, expected_trail_length_px=None: (

                        predicted_x,

                        predicted_y,

                        1.0,

                        5.2,

                        200.0,

                        1000.0,

                    ),

                ),

            ):

                estimate_visible_magnitude_limit(

                    source_path,

                    settings=settings,

                    catalog_service=catalog_service,

                    progress_callback=progress_messages.append,

                )



        self.assertTrue(any("Searching for 18.5-19.0 mag stars" in message for message in progress_messages))

        self.assertTrue(any("Match found" in message and "SNR=5.2" in message for message in progress_messages))

        self.assertTrue(any("mag bin complete" in message for message in progress_messages))



    def test_estimate_visible_magnitude_limit_uses_configurable_settings_and_annotations(self) -> None:

        with tempfile.TemporaryDirectory() as temp_dir:

            root_path = Path(temp_dir)

            settings = AppSettings.from_root(root_path)

            settings.asteroid_estimate_snr_threshold = 6.0

            settings.asteroid_estimate_start_magnitude = 18.5

            settings.asteroid_estimate_stars_per_bin = 3

            settings.asteroid_estimate_required_visible_stars = 2

            settings.asteroid_estimate_annotate_lowest_mag_stars = True

            source_path = root_path / "demo.fits"

            source_path.write_bytes(b"demo")

            solved_field = SolvedField(

                center_ra_deg=10.0,

                center_dec_deg=20.0,

                radius_deg=0.1,

                width=60,

                height=60,

                wcs_path=source_path,

            )

            wcs = WCS(naxis=2)

            wcs.wcs.crpix = [25.0, 25.0]

            wcs.wcs.cdelt = np.array([-1.0 / 3600.0, 1.0 / 3600.0])

            wcs.wcs.crval = [10.0, 20.0]

            wcs.wcs.ctype = ["RA---TAN", "DEC--TAN"]

            stars = [

                CatalogStar(

                    catalog="gaia-dr3",

                    source_id="bright-1",

                    name="Bright 1",

                    ra_deg=float(wcs.pixel_to_world_values(20.0, 20.0)[0]),

                    dec_deg=float(wcs.pixel_to_world_values(20.0, 20.0)[1]),

                    magnitude=16.0,

                    is_variable=False,

                ),

                CatalogStar(

                    catalog="gaia-dr3",

                    source_id="faint-1",

                    name="Faint 1",

                    ra_deg=float(wcs.pixel_to_world_values(30.0, 30.0)[0]),

                    dec_deg=float(wcs.pixel_to_world_values(30.0, 30.0)[1]),

                    magnitude=18.6,

                    is_variable=False,

                ),

                CatalogStar(

                    catalog="gaia-dr3",

                    source_id="faint-2",

                    name="Faint 2",

                    ra_deg=float(wcs.pixel_to_world_values(32.0, 32.0)[0]),

                    dec_deg=float(wcs.pixel_to_world_values(32.0, 32.0)[1]),

                    magnitude=18.7,

                    is_variable=False,

                ),

                CatalogStar(

                    catalog="gaia-dr3",

                    source_id="faint-3",

                    name="Faint 3",

                    ra_deg=float(wcs.pixel_to_world_values(34.0, 34.0)[0]),

                    dec_deg=float(wcs.pixel_to_world_values(34.0, 34.0)[1]),

                    magnitude=18.8,

                    is_variable=False,

                ),

            ]

            catalog_service = _StubGaiaCatalogService(stars)



            def measure_side_effect(_image_data, predicted_x, predicted_y, expected_trail_length_px=None):

                if predicted_x < 31.0:

                    return predicted_x, predicted_y, 0.5, 6.3, 200.0, 1000.0

                if predicted_x < 33.0:

                    return predicted_x, predicted_y, 0.7, 6.1, 190.0, 900.0

                return predicted_x, predicted_y, 0.8, 5.4, 180.0, 850.0



            with (

                patch("photometry_app.core.solar_system._resolve_source_field", return_value=(solved_field, False)),

                patch("photometry_app.core.solar_system._resolved_wcs_for_field", return_value=wcs),

                patch("photometry_app.core.solar_system.read_photometry_image_data", return_value=np.zeros((60, 60), dtype=float)),

                patch("photometry_app.core.solar_system._measure_local_match", side_effect=measure_side_effect),

            ):

                result = estimate_visible_magnitude_limit(

                    source_path,

                    settings=settings,

                    catalog_service=catalog_service,

                )



        self.assertAlmostEqual(result.dimmest_visible_magnitude, 18.7)

        self.assertEqual(result.visible_star_count, 2)

        self.assertEqual(result.tested_star_count, 3)

        self.assertEqual([star.name for star in result.annotated_stars], ["Faint 2", "Faint 1"])



    def test_build_multi_known_object_heliocentric_context_allows_empty_custom_window(self) -> None:

        window_start = datetime(2026, 1, 1, tzinfo=UTC)

        window_end = datetime(2026, 4, 1, tzinfo=UTC)

        captured_queries: list[dict[str, object]] = []



        class _FakeHorizons:

            def __init__(self, **kwargs: object) -> None:

                self._kwargs = dict(kwargs)

                captured_queries.append(self._kwargs)



            def vectors(self) -> Table:

                epochs = self._kwargs["epochs"]

                epoch_count = len(epochs) if isinstance(epochs, list) else 1

                rows = [

                    (

                        "Earth (399)",

                        1.0 + (index * 0.01),

                        0.1,

                        0.0,

                        0.01,

                        0.02,

                        0.03,

                    )

                    for index in range(epoch_count)

                ]

                return Table(rows=rows, names=("targetname", "x", "y", "z", "vx", "vy", "vz"))



        with patch("photometry_app.core.solar_system.Horizons", _FakeHorizons):

            context = build_multi_known_object_heliocentric_context(

                (),

                window_start=window_start,

                window_end=window_end,

                sample_count=5,

            )



        self.assertEqual(context.object_label, "Trajectory View")

        self.assertEqual(context.resolved_target_name, "Earth only")

        self.assertEqual(context.object_path_samples, ())

        self.assertEqual(len(context.earth_path_samples), 5)

        self.assertEqual(context.window_start, window_start)

        self.assertEqual(context.window_end, window_end)

        self.assertTrue(any(str(query.get("id")) == "399" for query in captured_queries))



    def test_altitude_deg_returns_none_when_iers_lookup_fails(self) -> None:

        location = EarthLocation(lat=35.0 * u.deg, lon=-105.0 * u.deg, height=1500.0 * u.m)



        with patch("photometry_app.core.solar_system.SkyCoord.transform_to", side_effect=RuntimeError("iers offline")):

            altitude = _altitude_deg(10.0, 20.0, datetime(2025, 1, 14, 21, 12, tzinfo=UTC), location)



        self.assertIsNone(altitude)