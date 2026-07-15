from __future__ import annotations

import json
import tempfile
import unittest
from datetime import datetime, timedelta
from pathlib import Path
from unittest.mock import patch

import numpy as np
import astropy.units as u
from astropy.coordinates import EarthLocation
from astropy.io import fits
from astropy.time import Time
from matplotlib.figure import Figure
from matplotlib.colors import to_hex
from PIL import Image

from photometry_app.core import exporters as exporters_module
from photometry_app.core.exporters import ScienceExportMetadata, export_annotated_images, export_light_curve_animated_gif, export_light_curve_plots, export_processing_report, preview_aavso_extended_export
from photometry_app.core.models import CatalogStar, FieldCatalog, LightCurvePoint, LightCurveSeries, PhotometryMeasurement, ProcessingReport, VariableStarLimitMode
from photometry_app.core.plotting import AnnotatedImageDisplay, FitPeriodInferenceResult, LightCurveFitConfig, LightCurvePlotPayload, _supports_periodic_auto_fit, build_light_curve_plot_payload, estimate_period_hours, infer_fit_period_hours, infer_fit_period_result, period_result_hits_search_edge, plot_annotated_image, plot_light_curve, plot_light_curve_payload


class ExportersTest(unittest.TestCase):
    def test_measurement_airmass_caches_computed_values(self) -> None:
        measurement = PhotometryMeasurement(
            source_id="1",
            source_name="R Cas",
            catalog="vsx",
            object_name="R_Cas",
            file_path=Path("missing_header.fits"),
            observation_time=datetime(2026, 3, 16, 1, 0, 0),
            filter_name="R",
            ra_deg=10.0,
            dec_deg=20.0,
            x=100.0,
            y=100.0,
            flux=5000.0,
            flux_error=15.0,
            instrumental_magnitude=-9.2,
            differential_magnitude=-0.2,
            is_variable=True,
            is_reference=False,
            flags=[],
        )
        metadata = ScienceExportMetadata(
            observing_site_latitude_deg=51.5074,
            observing_site_longitude_deg=-0.1278,
            observing_site_elevation_m=35.0,
        )

        exporters_module._computed_airmass_cached.cache_clear()
        try:
            with patch("photometry_app.core.exporters._computed_airmass_uncached", return_value=1.234) as compute_airmass:
                first = exporters_module.measurement_airmass(measurement, metadata)
                second = exporters_module.measurement_airmass(measurement, metadata)
        finally:
            exporters_module._computed_airmass_cached.cache_clear()

        self.assertEqual(first, 1.234)
        self.assertEqual(second, 1.234)
        compute_airmass.assert_called_once_with(
            measurement.observation_time,
            measurement.ra_deg,
            measurement.dec_deg,
            metadata.observing_site_latitude_deg,
            metadata.observing_site_longitude_deg,
            metadata.observing_site_elevation_m,
        )

    def test_plot_annotated_image_uses_top_left_origin(self) -> None:
        figure = Figure(figsize=(4, 4))
        axis = figure.add_subplot(111)
        image_path = Path("frame.fits")
        measurement = PhotometryMeasurement(
            source_id="1",
            source_name="R Cas",
            catalog="vsx",
            object_name="R_Cas",
            file_path=image_path,
            observation_time=datetime(2026, 3, 16, 1, 0, 0),
            filter_name="R",
            ra_deg=10.0,
            dec_deg=20.0,
            x=1.0,
            y=2.0,
            flux=5000.0,
            flux_error=15.0,
            instrumental_magnitude=-9.2,
            differential_magnitude=-0.2,
            is_variable=True,
            is_reference=False,
            flags=[],
        )

        plot_annotated_image(
            axis,
            image_path,
            measurement,
            [],
            "Unable to render the selected image.",
            display=AnnotatedImageDisplay(
                image_path=image_path,
                normalized_data=np.arange(9, dtype=float).reshape(3, 3),
                norm=None,
            ),
        )

        self.assertEqual(axis.images[0].origin, "upper")

    def test_export_processing_report_writes_expected_files(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            report = ProcessingReport(
                object_name="R_Cas",
                files_processed=2,
                solved_files=2,
                field_catalog=FieldCatalog(10.0, 20.0, 0.5, variable_stars=[CatalogStar("vsx", "1", "R Cas", 10.0, 20.0, 7.0, True)]),
                total_variable_stars_found=10,
                variable_star_limit_mode=VariableStarLimitMode.PERCENT,
                variable_star_limit_value=10,
                reference_stars=[CatalogStar("gaia-dr3", "2", "2", 10.2, 20.2, 11.0, False)],
                measurements=[
                    PhotometryMeasurement(
                        source_id="1",
                        source_name="R Cas",
                        catalog="vsx",
                        object_name="R_Cas",
                        file_path=root / "Files" / "R_Cas" / "a.fits",
                        observation_time=datetime(2026, 3, 16, 1, 0, 0),
                        filter_name="R",
                        ra_deg=10.0,
                        dec_deg=20.0,
                        x=100.0,
                        y=100.0,
                        flux=5000.0,
                        flux_error=15.0,
                        instrumental_magnitude=-9.2,
                        differential_magnitude=-0.2,
                        differential_magnitude_error=0.012,
                        is_variable=True,
                        is_reference=False,
                        flags=[],
                    )
                ],
                light_curves=[
                    LightCurveSeries(
                        object_name="R_Cas",
                        source_id="1",
                        source_name="R Cas",
                        filter_name="R",
                        points=[
                            LightCurvePoint(
                                observation_time=datetime(2026, 3, 16, 1, 0, 0),
                                file_path=root / "Files" / "R_Cas" / "a.fits",
                                differential_magnitude=-0.2,
                                instrumental_magnitude=-9.2,
                                flux=5000.0,
                                flux_error=15.0,
                                differential_magnitude_error=0.012,
                            )
                        ],
                    )
                ],
                notes=["Test export note"],
            )

            export_processing_report(report, root / "exports")

            self.assertTrue((root / "exports" / "R_Cas_measurements.csv").exists())
            self.assertTrue((root / "exports" / "R_Cas_light_curves.csv").exists())
            self.assertTrue((root / "exports" / "R_Cas_summary.json").exists())
            self.assertTrue((root / "exports" / "R_Cas_accepted_observations.csv").exists())
            self.assertTrue((root / "exports" / "R_Cas_accepted_observations.json").exists())
            self.assertTrue((root / "exports" / "R_Cas_rejected_observations.csv").exists())
            self.assertTrue((root / "exports" / "R_Cas_rejected_observations.json").exists())
            self.assertTrue((root / "exports" / "R_Cas_reference_manifest.csv").exists())
            self.assertTrue((root / "exports" / "R_Cas_reduction_manifest.json").exists())
            self.assertTrue((root / "exports" / "R_Cas_provenance_manifest.json").exists())
            self.assertTrue((root / "exports" / "R_Cas_aavso_extended.txt").exists())
            self.assertTrue((root / "exports" / "R_Cas_aavso_preflight.json").exists())
            self.assertTrue((root / "exports" / "R_Cas_README.txt").exists())
            self.assertTrue((root / "exports" / "R_Cas_plots" / "R_Cas_R.png").exists())
            self.assertFalse((root / "exports" / "R_Cas_annotated_images").exists())

            summary_payload = json.loads((root / "exports" / "R_Cas_summary.json").read_text(encoding="utf-8"))
            self.assertEqual(summary_payload["light_curve_count"], 1)
            self.assertEqual(summary_payload["measurement_count"], 1)
            self.assertEqual(summary_payload["accepted_observation_count"], 1)
            self.assertEqual(summary_payload["rejected_observation_count"], 0)
            self.assertEqual(summary_payload["variable_stars_found"], 10)
            self.assertEqual(summary_payload["variable_stars_analyzed"], 1)
            self.assertEqual(summary_payload["variable_star_limit_mode"], "percent")
            self.assertEqual(summary_payload["variable_star_limit_value"], 10)
            measurements_csv = (root / "exports" / "R_Cas_measurements.csv").read_text(encoding="utf-8")
            light_curves_csv = (root / "exports" / "R_Cas_light_curves.csv").read_text(encoding="utf-8")
            readme_text = (root / "exports" / "R_Cas_README.txt").read_text(encoding="utf-8")
            self.assertIn("differential_magnitude_error", measurements_csv)
            self.assertIn("0.012", measurements_csv)
            self.assertIn("differential_magnitude_error", light_curves_csv)
            self.assertIn("0.012", light_curves_csv)
            self.assertIn("Upload this file to AAVSO: R_Cas_aavso_extended.txt", readme_text)

    def test_export_processing_report_supports_custom_export_name_stem(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            report = ProcessingReport(
                object_name="AE_UMa",
                files_processed=1,
                solved_files=1,
                field_catalog=FieldCatalog(10.0, 20.0, 0.5, variable_stars=[CatalogStar("vsx", "1", "Target", 10.0, 20.0, 7.0, True)]),
                total_variable_stars_found=1,
                variable_star_limit_mode=VariableStarLimitMode.PERCENT,
                variable_star_limit_value=100,
                reference_stars=[],
                measurements=[
                    PhotometryMeasurement(
                        source_id="1",
                        source_name="Target",
                        catalog="vsx",
                        object_name="AE_UMa",
                        file_path=root / "Files" / "AE_UMa" / "a.fits",
                        observation_time=datetime(2026, 3, 16, 1, 0, 0),
                        filter_name="R",
                        ra_deg=10.0,
                        dec_deg=20.0,
                        x=100.0,
                        y=100.0,
                        flux=5000.0,
                        flux_error=15.0,
                        instrumental_magnitude=-9.2,
                        differential_magnitude=-0.2,
                        differential_magnitude_error=0.012,
                        is_variable=True,
                        is_reference=False,
                        flags=[],
                    )
                ],
                light_curves=[
                    LightCurveSeries(
                        object_name="AE_UMa",
                        source_id="1",
                        source_name="Target",
                        filter_name="R",
                        points=[
                            LightCurvePoint(
                                observation_time=datetime(2026, 3, 16, 1, 0, 0),
                                file_path=root / "Files" / "AE_UMa" / "a.fits",
                                differential_magnitude=-0.2,
                                differential_magnitude_error=0.012,
                                instrumental_magnitude=-9.2,
                                flux=5000.0,
                                flux_error=15.0,
                            )
                        ],
                    )
                ],
                notes=[],
            )

            result = export_processing_report(report, root / "exports", export_name_stem="GSC_02998-00537")

            self.assertTrue((root / "exports" / "GSC_02998-00537_measurements.csv").exists())
            self.assertTrue((root / "exports" / "GSC_02998-00537_aavso_extended.txt").exists())
            self.assertTrue((root / "exports" / "GSC_02998-00537_README.txt").exists())
            self.assertEqual(result["aavso_extended_txt"], root / "exports" / "GSC_02998-00537_aavso_extended.txt")
            self.assertEqual(result["science_export_readme_txt"], root / "exports" / "GSC_02998-00537_README.txt")

    def test_export_processing_report_supports_filtered_subset(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            report = ProcessingReport(
                object_name="R_Cas",
                files_processed=2,
                solved_files=2,
                field_catalog=FieldCatalog(10.0, 20.0, 0.5, variable_stars=[CatalogStar("vsx", "1", "R Cas", 10.0, 20.0, 7.0, True)]),
                total_variable_stars_found=10,
                variable_star_limit_mode=VariableStarLimitMode.PERCENT,
                variable_star_limit_value=10,
                reference_stars=[CatalogStar("gaia-dr3", "2", "2", 10.2, 20.2, 11.0, False)],
                measurements=[
                    PhotometryMeasurement(
                        source_id="1",
                        source_name="R Cas",
                        catalog="vsx",
                        object_name="R_Cas",
                        file_path=root / "Files" / "R_Cas" / "a.fits",
                        observation_time=datetime(2026, 3, 16, 1, 0, 0),
                        filter_name="R",
                        ra_deg=10.0,
                        dec_deg=20.0,
                        x=100.0,
                        y=100.0,
                        flux=5000.0,
                        flux_error=15.0,
                        instrumental_magnitude=-9.2,
                        differential_magnitude=-0.2,
                        is_variable=True,
                        is_reference=False,
                        flags=["edge"],
                    ),
                    PhotometryMeasurement(
                        source_id="2",
                        source_name="ref",
                        catalog="gaia-dr3",
                        object_name="R_Cas",
                        file_path=root / "Files" / "R_Cas" / "b.fits",
                        observation_time=datetime(2026, 3, 16, 2, 0, 0),
                        filter_name="R",
                        ra_deg=10.2,
                        dec_deg=20.2,
                        x=101.0,
                        y=101.0,
                        flux=7000.0,
                        flux_error=10.0,
                        instrumental_magnitude=-9.7,
                        differential_magnitude=None,
                        is_variable=False,
                        is_reference=True,
                        flags=[],
                    ),
                ],
                light_curves=[
                    LightCurveSeries(
                        object_name="R_Cas",
                        source_id="1",
                        source_name="R Cas",
                        filter_name="R",
                        points=[
                            LightCurvePoint(
                                observation_time=datetime(2026, 3, 16, 1, 0, 0),
                                file_path=root / "Files" / "R_Cas" / "a.fits",
                                differential_magnitude=-0.2,
                                instrumental_magnitude=-9.2,
                                flux=5000.0,
                                flux_error=15.0,
                            )
                        ],
                    )
                ],
                notes=["Test export note"],
            )

            result = export_processing_report(
                report,
                root / "exports",
                measurements=[report.measurements[0]],
                light_curves=report.light_curves,
                export_suffix="flagged_only",
            )

            self.assertTrue((root / "exports" / "R_Cas_flagged_only_measurements.csv").exists())
            self.assertTrue((root / "exports" / "R_Cas_flagged_only_light_curves.csv").exists())
            self.assertTrue((root / "exports" / "R_Cas_flagged_only_summary.json").exists())
            summary_payload = json.loads((root / "exports" / "R_Cas_flagged_only_summary.json").read_text(encoding="utf-8"))
            self.assertEqual(summary_payload["measurement_count"], 1)
            self.assertEqual(summary_payload["variable_stars_found"], 10)
            self.assertEqual(summary_payload["variable_stars_analyzed"], 1)
            self.assertEqual(summary_payload["variable_star_limit_mode"], "percent")
            self.assertEqual(summary_payload["variable_star_limit_value"], 10)
            self.assertEqual(result["plot_count"], 1)
            self.assertEqual(result["annotated_image_count"], 0)
            self.assertEqual(result["accepted_observation_count"], 1)
            self.assertEqual(result["rejected_observation_count"], 0)
            self.assertEqual(result["aavso_observation_count"], 1)
            self.assertGreaterEqual(result["aavso_preflight_warning_count"], 1)
            csv_text = (root / "exports" / "R_Cas_flagged_only_measurements.csv").read_text(encoding="utf-8")
            self.assertIn("R Cas", csv_text)
            self.assertNotIn(",ref,", csv_text)

    def test_export_processing_report_writes_science_ready_bundle_with_metadata(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            measurement_path = root / "Files" / "R_Cas" / "a.fits"
            rejected_path = root / "Files" / "R_Cas" / "b.fits"
            report = ProcessingReport(
                object_name="R_Cas",
                files_processed=2,
                solved_files=2,
                field_catalog=FieldCatalog(10.0, 20.0, 0.5, variable_stars=[CatalogStar("vsx", "1", "R Cas", 10.0, 20.0, 7.0, True)]),
                total_variable_stars_found=1,
                variable_star_limit_mode=VariableStarLimitMode.PERCENT,
                variable_star_limit_value=100,
                reference_stars=[CatalogStar("gaia-dr3", "2", "Comp 1", 10.2, 20.2, 11.0, False)],
                measurements=[
                    PhotometryMeasurement(
                        source_id="1",
                        source_name="R Cas",
                        catalog="vsx",
                        object_name="R_Cas",
                        file_path=measurement_path,
                        observation_time=datetime(2026, 3, 16, 1, 0, 0),
                        filter_name="R",
                        ra_deg=10.0,
                        dec_deg=20.0,
                        x=100.0,
                        y=100.0,
                        flux=5000.0,
                        flux_error=15.0,
                        instrumental_magnitude=-9.2,
                        differential_magnitude=-0.2,
                        differential_magnitude_error=0.012,
                        is_variable=True,
                        is_reference=False,
                        comparison_source_ids=["2"],
                        comparison_source_names=["Comp 1"],
                        comparison_reference_flux=7000.0,
                        comparison_scatter=0.01,
                        flags=[],
                        aperture_radius=4.0,
                        annulus_inner_radius=7.0,
                        annulus_outer_radius=10.0,
                    ),
                    PhotometryMeasurement(
                        source_id="1",
                        source_name="R Cas",
                        catalog="vsx",
                        object_name="R_Cas",
                        file_path=rejected_path,
                        observation_time=datetime(2026, 3, 16, 2, 0, 0),
                        filter_name="R",
                        ra_deg=10.0,
                        dec_deg=20.0,
                        x=100.0,
                        y=100.0,
                        flux=4900.0,
                        flux_error=18.0,
                        instrumental_magnitude=-9.1,
                        differential_magnitude=-0.1,
                        differential_magnitude_error=0.02,
                        is_variable=True,
                        is_reference=False,
                        flags=["Low SNR (2.5)."],
                        excluded_from_analysis=True,
                        exclusion_reasons=["SNR 2.5 is below the analysis threshold."],
                    ),
                    PhotometryMeasurement(
                        source_id="2",
                        source_name="Comp 1",
                        catalog="gaia-dr3",
                        object_name="R_Cas",
                        file_path=measurement_path,
                        observation_time=datetime(2026, 3, 16, 1, 0, 0),
                        filter_name="R",
                        ra_deg=10.2,
                        dec_deg=20.2,
                        x=101.0,
                        y=101.0,
                        flux=7000.0,
                        flux_error=10.0,
                        instrumental_magnitude=-9.7,
                        differential_magnitude=None,
                        is_variable=False,
                        is_reference=True,
                        flags=[],
                        snr=55.0,
                    ),
                ],
                light_curves=[
                    LightCurveSeries(
                        object_name="R_Cas",
                        source_id="1",
                        source_name="R Cas",
                        filter_name="R",
                        points=[
                            LightCurvePoint(
                                observation_time=datetime(2026, 3, 16, 1, 0, 0),
                                file_path=measurement_path,
                                differential_magnitude=-0.2,
                                differential_magnitude_error=0.012,
                                instrumental_magnitude=-9.2,
                                flux=5000.0,
                                flux_error=15.0,
                            )
                        ],
                    )
                ],
            )

            export_processing_report(
                report,
                root / "exports",
                science_metadata=ScienceExportMetadata(
                    observer_code="KAY",
                    observer_name="Kay",
                    site_name="Backyard",
                    telescope="80mm refractor",
                    camera="Mono CMOS",
                    reduction_notes="Cleaned points only.",
                ),
            )

            accepted_csv = (root / "exports" / "R_Cas_accepted_observations.csv").read_text(encoding="utf-8")
            accepted_json = json.loads((root / "exports" / "R_Cas_accepted_observations.json").read_text(encoding="utf-8"))
            rejected_csv = (root / "exports" / "R_Cas_rejected_observations.csv").read_text(encoding="utf-8")
            rejected_json = json.loads((root / "exports" / "R_Cas_rejected_observations.json").read_text(encoding="utf-8"))
            reference_csv = (root / "exports" / "R_Cas_reference_manifest.csv").read_text(encoding="utf-8")
            reduction_manifest = json.loads((root / "exports" / "R_Cas_reduction_manifest.json").read_text(encoding="utf-8"))
            provenance_manifest = json.loads((root / "exports" / "R_Cas_provenance_manifest.json").read_text(encoding="utf-8"))

            self.assertIn("julian_date_utc", accepted_csv)
            self.assertIn("R Cas", accepted_csv)
            self.assertEqual(accepted_json["schema"], "science-observation-v3")
            self.assertEqual(accepted_json["observation_count"], 1)
            self.assertEqual(accepted_json["rows"][0]["source_name"], "R Cas")
            self.assertEqual(accepted_json["rows"][0]["comparison_source_count"], 1)
            self.assertEqual(accepted_json["rows"][0]["comparison_method"], "single")
            self.assertEqual(accepted_json["rows"][0]["aperture_radius"], 4.0)
            self.assertEqual(accepted_json["rows"][0]["annulus_inner_radius"], 7.0)
            self.assertEqual(accepted_json["rows"][0]["annulus_outer_radius"], 10.0)
            self.assertEqual(accepted_json["rows"][0]["calibration_state"], "zero_point_calibrated_untransformed")
            self.assertEqual(accepted_json["rows"][0]["calibrated_magnitude"], 11.5)
            self.assertEqual(accepted_json["rows"][0]["zero_point_magnitude"], 20.7)
            self.assertEqual(accepted_json["rows"][0]["zero_point_source_count"], 1)
            self.assertEqual(accepted_json["rows"][0]["measurement_kind"], "calibrated_magnitude")
            self.assertEqual(accepted_json["rows"][0]["reported_value"], 11.5)
            self.assertEqual(accepted_json["rows"][0]["reported_uncertainty"], 0.012)
            self.assertFalse(accepted_json["rows"][0]["transformed_requested"])
            self.assertEqual(accepted_json["rows"][0]["aavso_mtype_candidate"], "STD")
            self.assertNotIn("SNR 2.5 is below the analysis threshold.", accepted_csv)
            self.assertIn("SNR 2.5 is below the analysis threshold.", rejected_csv)
            self.assertEqual(rejected_json["schema"], "science-observation-v3")
            self.assertEqual(rejected_json["observation_count"], 1)
            self.assertEqual(rejected_json["rows"][0]["excluded_from_analysis"], True)
            self.assertEqual(rejected_json["rows"][0]["comparison_method"], "none")
            self.assertEqual(rejected_json["rows"][0]["calibration_state"], "differential_only")
            self.assertEqual(rejected_json["rows"][0]["aavso_mtype_candidate"], "DIF")
            self.assertIn("accepted_usage_count", reference_csv)
            self.assertIn("Comp 1", reference_csv)
            self.assertEqual(reduction_manifest["observation_schema"], "science-observation-v3")
            self.assertEqual(provenance_manifest["schema"], "science-provenance-v1")
            self.assertEqual(provenance_manifest["observation_schema"], "science-observation-v3")
            self.assertEqual(provenance_manifest["accepted_observation_count"], 1)
            self.assertEqual(provenance_manifest["rejected_observation_count"], 1)
            self.assertEqual(provenance_manifest["calibration_state_counts"]["zero_point_calibrated_untransformed"], 1)
            self.assertEqual(provenance_manifest["calibration_state_counts"]["differential_only"], 1)
            self.assertEqual(provenance_manifest["comparison_method_counts"]["single"], 1)
            self.assertEqual(provenance_manifest["comparison_method_counts"]["none"], 1)
            self.assertEqual(len(provenance_manifest["reference_stars"]), 1)
            self.assertEqual(reduction_manifest["submission_metadata"]["observer_code"], "KAY")
            self.assertEqual(reduction_manifest["accepted_observation_count"], 1)
            self.assertEqual(reduction_manifest["rejected_observation_count"], 1)
            self.assertEqual(reduction_manifest["filters"], ["R"])

    def test_export_processing_report_writes_aavso_extended_differential_file(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            measurement_path = root / "Files" / "R_Cas" / "a.fits"
            measurement_path.parent.mkdir(parents=True)
            fits.PrimaryHDU(data=np.ones((4, 4), dtype=float), header=fits.Header({"AIRMASS": 1.234})).writeto(measurement_path)
            report = ProcessingReport(
                object_name="R_Cas",
                files_processed=1,
                solved_files=1,
                field_catalog=FieldCatalog(10.0, 20.0, 0.5, variable_stars=[CatalogStar("vsx", "1", "R Cas", 10.0, 20.0, 7.0, True)]),
                total_variable_stars_found=1,
                variable_star_limit_mode=VariableStarLimitMode.PERCENT,
                variable_star_limit_value=100,
                reference_stars=[CatalogStar("gaia-dr3", "2", "Comp 1", 10.2, 20.2, 11.0, False)],
                measurements=[
                    PhotometryMeasurement(
                        source_id="1",
                        source_name="R Cas",
                        catalog="vsx",
                        object_name="R_Cas",
                        file_path=measurement_path,
                        observation_time=datetime(2026, 3, 16, 1, 0, 0),
                        filter_name="R",
                        ra_deg=10.0,
                        dec_deg=20.0,
                        x=100.0,
                        y=100.0,
                        flux=5000.0,
                        flux_error=15.0,
                        instrumental_magnitude=-9.2,
                        differential_magnitude=-0.2,
                        differential_magnitude_error=0.012,
                        is_variable=True,
                        is_reference=False,
                        comparison_source_ids=["2"],
                        comparison_source_names=["Comp 1"],
                        flags=[],
                    ),
                    PhotometryMeasurement(
                        source_id="2",
                        source_name="Comp 1",
                        catalog="gaia-dr3",
                        object_name="R_Cas",
                        file_path=measurement_path,
                        observation_time=datetime(2026, 3, 16, 1, 0, 0),
                        filter_name="R",
                        ra_deg=10.2,
                        dec_deg=20.2,
                        x=101.0,
                        y=101.0,
                        flux=7000.0,
                        flux_error=10.0,
                        instrumental_magnitude=-9.7,
                        differential_magnitude=None,
                        is_variable=False,
                        is_reference=True,
                        flags=[],
                    ),
                    PhotometryMeasurement(
                        source_id="3",
                        source_name="Check 1",
                        catalog="gaia-dr3",
                        object_name="R_Cas",
                        file_path=measurement_path,
                        observation_time=datetime(2026, 3, 16, 1, 0, 0),
                        filter_name="R",
                        ra_deg=10.3,
                        dec_deg=20.3,
                        x=102.0,
                        y=102.0,
                        flux=6500.0,
                        flux_error=11.0,
                        instrumental_magnitude=-9.4,
                        differential_magnitude=-0.05,
                        is_variable=False,
                        is_reference=False,
                        is_check=True,
                        flags=[],
                    ),
                ],
                light_curves=[
                    LightCurveSeries(
                        object_name="R_Cas",
                        source_id="1",
                        source_name="R Cas",
                        filter_name="R",
                        points=[
                            LightCurvePoint(
                                observation_time=datetime(2026, 3, 16, 1, 0, 0),
                                file_path=measurement_path,
                                differential_magnitude=-0.2,
                                differential_magnitude_error=0.012,
                                instrumental_magnitude=-9.2,
                                flux=5000.0,
                                flux_error=15.0,
                            )
                        ],
                    )
                ],
                notes=[],
            )

            result = export_processing_report(
                report,
                root / "exports",
                science_metadata=ScienceExportMetadata(
                    observer_code="KAY",
                    aavso_chart_id="X12345ABC",
                    transformed=False,
                    reduction_notes="Untransformed standard export",
                ),
            )

            aavso_text = (root / "exports" / "R_Cas_aavso_extended.txt").read_text(encoding="ascii")
            preflight = json.loads((root / "exports" / "R_Cas_aavso_preflight.json").read_text(encoding="utf-8"))

            self.assertEqual(result["aavso_observation_count"], 1)
            self.assertEqual(result["aavso_extended_txt"], root / "exports" / "R_Cas_aavso_extended.txt")
            self.assertEqual(result["aavso_preflight_json"], root / "exports" / "R_Cas_aavso_preflight.json")
            self.assertIn("#TYPE=EXTENDED", aavso_text)
            self.assertIn("#OBSCODE=KAY", aavso_text)
            self.assertIn("#SOFTWARE=Citizen Photometry", aavso_text)
            self.assertIn("#DELIM=,", aavso_text)
            self.assertIn("#NAME,DATE,MAG,MERR,FILT,TRANS,MTYPE,CNAME,CMAG,KNAME,KMAG,AMASS,GROUP,CHART,NOTES", aavso_text)
            self.assertIn(",11.5,0.012,R,NO,STD,Comp 1,-9.7,Check 1,-9.4,1.234,na,X12345ABC,Untransformed standard export Zero-point sources: 1|VMAGINS=-9.2|CMAGINS=-9.7|CREFMAG=11.0|KMAGINS=-9.4|KREFMAG=11.3", aavso_text)
            self.assertTrue(preflight["chart_id_present"])
            self.assertEqual(preflight["standard_observation_count"], 1)
            self.assertEqual(preflight["differential_observation_count"], 0)
            self.assertEqual(preflight["warning_count"], 0)

    def test_export_processing_report_prefers_science_export_filter_setting(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            measurement_path = root / "Files" / "Demo" / "a.fits"
            measurement_path.parent.mkdir(parents=True)
            fits.PrimaryHDU(data=np.ones((4, 4), dtype=float), header=fits.Header({"AIRMASS": 1.111})).writeto(measurement_path)
            report = ProcessingReport(
                object_name="Demo",
                files_processed=1,
                solved_files=1,
                field_catalog=FieldCatalog(10.0, 20.0, 0.5, variable_stars=[CatalogStar("vsx", "1", "Target", 10.0, 20.0, 12.0, True)]),
                total_variable_stars_found=1,
                variable_star_limit_mode=VariableStarLimitMode.PERCENT,
                variable_star_limit_value=100,
                reference_stars=[],
                measurements=[
                    PhotometryMeasurement(
                        source_id="1",
                        source_name="Target",
                        catalog="vsx",
                        object_name="Demo",
                        file_path=measurement_path,
                        observation_time=datetime(2026, 3, 16, 1, 0, 0),
                        filter_name="L",
                        ra_deg=10.0,
                        dec_deg=20.0,
                        x=100.0,
                        y=100.0,
                        flux=5000.0,
                        flux_error=15.0,
                        instrumental_magnitude=-9.2,
                        differential_magnitude=-0.2,
                        differential_magnitude_error=0.012,
                        is_variable=True,
                        is_reference=False,
                        flags=[],
                    )
                ],
                light_curves=[],
                notes=[],
            )

            export_processing_report(
                report,
                root / "exports",
                science_metadata=ScienceExportMetadata(observer_code="KAY", aavso_chart_id="X12345ABC", filter_system="CV"),
            )

            export_line = [
                line for line in (root / "exports" / "Demo_aavso_extended.txt").read_text(encoding="ascii").splitlines()
                if line and not line.startswith("#")
            ][0]

            self.assertIn(",CV,NO,DIF,", export_line)
            self.assertIn("Source filter: L; exported as CV per settings", export_line)

    def test_export_processing_report_writes_aavso_extended_standard_file_when_transformed(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            measurement_path = root / "Files" / "R_Cas" / "a.fits"
            measurement_path.parent.mkdir(parents=True)
            fits.PrimaryHDU(data=np.ones((4, 4), dtype=float), header=fits.Header({"AIRMASS": 1.111})).writeto(measurement_path)
            report = ProcessingReport(
                object_name="R_Cas",
                files_processed=1,
                solved_files=1,
                field_catalog=FieldCatalog(10.0, 20.0, 0.5, variable_stars=[CatalogStar("vsx", "1", "R Cas", 10.0, 20.0, 7.0, True)]),
                total_variable_stars_found=1,
                variable_star_limit_mode=VariableStarLimitMode.PERCENT,
                variable_star_limit_value=100,
                reference_stars=[CatalogStar("gaia-dr3", "2", "Comp 1", 10.2, 20.2, 11.0, False)],
                measurements=[
                    PhotometryMeasurement(
                        source_id="1",
                        source_name="R Cas",
                        catalog="vsx",
                        object_name="R_Cas",
                        file_path=measurement_path,
                        observation_time=datetime(2026, 3, 16, 1, 0, 0),
                        filter_name="R",
                        ra_deg=10.0,
                        dec_deg=20.0,
                        x=100.0,
                        y=100.0,
                        flux=5000.0,
                        flux_error=15.0,
                        instrumental_magnitude=-9.2,
                        differential_magnitude=-0.2,
                        differential_magnitude_error=0.012,
                        is_variable=True,
                        is_reference=False,
                        comparison_source_ids=["2"],
                        comparison_source_names=["Comp 1"],
                        flags=[],
                    ),
                    PhotometryMeasurement(
                        source_id="2",
                        source_name="Comp 1",
                        catalog="gaia-dr3",
                        object_name="R_Cas",
                        file_path=measurement_path,
                        observation_time=datetime(2026, 3, 16, 1, 0, 0),
                        filter_name="R",
                        ra_deg=10.2,
                        dec_deg=20.2,
                        x=101.0,
                        y=101.0,
                        flux=7000.0,
                        flux_error=10.0,
                        instrumental_magnitude=-9.7,
                        differential_magnitude=None,
                        is_variable=False,
                        is_reference=True,
                        flags=[],
                    ),
                ],
                light_curves=[
                    LightCurveSeries(
                        object_name="R_Cas",
                        source_id="1",
                        source_name="R Cas",
                        filter_name="R",
                        points=[
                            LightCurvePoint(
                                observation_time=datetime(2026, 3, 16, 1, 0, 0),
                                file_path=measurement_path,
                                differential_magnitude=-0.2,
                                differential_magnitude_error=0.012,
                                instrumental_magnitude=-9.2,
                                flux=5000.0,
                                flux_error=15.0,
                            )
                        ],
                    )
                ],
                notes=[],
            )

            result = export_processing_report(
                report,
                root / "exports",
                science_metadata=ScienceExportMetadata(
                    observer_code="KAY",
                    aavso_chart_id="X12345ABC",
                    transformed=True,
                    reduction_notes="Transformed standard export",
                ),
            )

            aavso_text = (root / "exports" / "R_Cas_aavso_extended.txt").read_text(encoding="ascii")
            preflight = json.loads((root / "exports" / "R_Cas_aavso_preflight.json").read_text(encoding="utf-8"))

            self.assertEqual(result["aavso_standard_observation_count"], 1)
            self.assertEqual(result["aavso_differential_observation_count"], 0)
            self.assertIn(",11.5,0.012,R,YES,STD,Comp 1,-9.7,na,na,1.111,na,X12345ABC,Transformed standard export Zero-point sources: 1|VMAGINS=-9.2|CMAGINS=-9.7|CREFMAG=11.0", aavso_text)
            self.assertTrue(preflight["chart_id_present"])
            self.assertEqual(preflight["standard_observation_count"], 1)
            self.assertEqual(preflight["differential_observation_count"], 0)
            self.assertEqual(preflight["warning_count"], 0)

    def test_export_processing_report_uses_exposure_midpoint_for_aavso_jd(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            measurement_path = root / "Files" / "R_Cas" / "a.fits"
            measurement_path.parent.mkdir(parents=True)
            fits.PrimaryHDU(
                data=np.ones((4, 4), dtype=float),
                header=fits.Header({"EXPTIME": 120.0, "AIRMASS": 1.111}),
            ).writeto(measurement_path)
            start_time = datetime(2026, 3, 16, 1, 0, 0)
            report = ProcessingReport(
                object_name="R_Cas",
                files_processed=1,
                solved_files=1,
                field_catalog=FieldCatalog(10.0, 20.0, 0.5, variable_stars=[CatalogStar("vsx", "1", "R Cas", 10.0, 20.0, 7.0, True)]),
                total_variable_stars_found=1,
                variable_star_limit_mode=VariableStarLimitMode.PERCENT,
                variable_star_limit_value=100,
                reference_stars=[],
                measurements=[
                    PhotometryMeasurement(
                        source_id="1",
                        source_name="R Cas",
                        catalog="vsx",
                        object_name="R_Cas",
                        file_path=measurement_path,
                        observation_time=start_time,
                        filter_name="R",
                        ra_deg=10.0,
                        dec_deg=20.0,
                        x=100.0,
                        y=100.0,
                        flux=5000.0,
                        flux_error=15.0,
                        instrumental_magnitude=-9.2,
                        differential_magnitude=-0.2,
                        differential_magnitude_error=0.012,
                        is_variable=True,
                        is_reference=False,
                        flags=[],
                    )
                ],
                light_curves=[],
                notes=[],
            )

            export_processing_report(
                report,
                root / "exports",
                science_metadata=ScienceExportMetadata(observer_code="KAY", aavso_chart_id="X12345ABC"),
            )

            export_line = [
                line for line in (root / "exports" / "R_Cas_aavso_extended.txt").read_text(encoding="ascii").splitlines()
                if line and not line.startswith("#")
            ][0]
            expected_jd = f"{Time(start_time + timedelta(seconds=60.0), scale='utc').jd:.5f}"

            self.assertEqual(export_line.split(",")[1], expected_jd)

    def test_export_processing_report_records_check_residual_and_rms(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            frame_a = root / "Files" / "Demo" / "a.fits"
            frame_b = root / "Files" / "Demo" / "b.fits"
            frame_a.parent.mkdir(parents=True)
            fits.PrimaryHDU(data=np.ones((4, 4), dtype=float), header=fits.Header({"AIRMASS": 1.111})).writeto(frame_a)
            fits.PrimaryHDU(data=np.ones((4, 4), dtype=float), header=fits.Header({"AIRMASS": 1.111})).writeto(frame_b)

            report = ProcessingReport(
                object_name="Demo",
                files_processed=2,
                solved_files=2,
                field_catalog=FieldCatalog(10.0, 20.0, 0.5, variable_stars=[CatalogStar("vsx", "1", "Target", 10.0, 20.0, 12.0, True)]),
                total_variable_stars_found=1,
                variable_star_limit_mode=VariableStarLimitMode.PERCENT,
                variable_star_limit_value=100,
                reference_stars=[CatalogStar("gaia-dr3", "2", "Comp 1", 10.2, 20.2, 11.0, False)],
                measurements=[
                    PhotometryMeasurement(
                        source_id="1",
                        source_name="Target",
                        catalog="vsx",
                        object_name="Demo",
                        file_path=frame_a,
                        observation_time=datetime(2026, 3, 16, 1, 0, 0),
                        filter_name="CV",
                        ra_deg=10.0,
                        dec_deg=20.0,
                        x=100.0,
                        y=100.0,
                        flux=5000.0,
                        flux_error=15.0,
                        instrumental_magnitude=-9.2,
                        differential_magnitude=-0.2,
                        differential_magnitude_error=0.012,
                        is_variable=True,
                        is_reference=False,
                        comparison_source_ids=["2"],
                        comparison_source_names=["Comp 1"],
                        flags=[],
                        zero_point_magnitude=20.7,
                        zero_point_source_count=1,
                        calibrated_magnitude=11.5,
                        calibrated_magnitude_error=0.012,
                    ),
                    PhotometryMeasurement(
                        source_id="3",
                        source_name="Check 1",
                        catalog="gaia-dr3",
                        object_name="Demo",
                        file_path=frame_a,
                        observation_time=datetime(2026, 3, 16, 1, 0, 0),
                        filter_name="CV",
                        ra_deg=10.3,
                        dec_deg=20.3,
                        x=102.0,
                        y=102.0,
                        flux=6500.0,
                        flux_error=11.0,
                        instrumental_magnitude=-9.35,
                        differential_magnitude=-0.05,
                        is_variable=False,
                        is_reference=False,
                        is_check=True,
                        flags=[],
                        catalog_magnitude=11.3,
                    ),
                    PhotometryMeasurement(
                        source_id="2",
                        source_name="Comp 1",
                        catalog="gaia-dr3",
                        object_name="Demo",
                        file_path=frame_a,
                        observation_time=datetime(2026, 3, 16, 1, 0, 0),
                        filter_name="CV",
                        ra_deg=10.2,
                        dec_deg=20.2,
                        x=101.0,
                        y=101.0,
                        flux=7000.0,
                        flux_error=10.0,
                        instrumental_magnitude=-9.7,
                        differential_magnitude=None,
                        is_variable=False,
                        is_reference=True,
                        flags=[],
                    ),
                    PhotometryMeasurement(
                        source_id="1",
                        source_name="Target",
                        catalog="vsx",
                        object_name="Demo",
                        file_path=frame_b,
                        observation_time=datetime(2026, 3, 16, 2, 0, 0),
                        filter_name="CV",
                        ra_deg=10.0,
                        dec_deg=20.0,
                        x=100.0,
                        y=100.0,
                        flux=5100.0,
                        flux_error=15.0,
                        instrumental_magnitude=-9.1,
                        differential_magnitude=-0.1,
                        differential_magnitude_error=0.013,
                        is_variable=True,
                        is_reference=False,
                        comparison_source_ids=["2"],
                        comparison_source_names=["Comp 1"],
                        flags=[],
                        zero_point_magnitude=20.7,
                        zero_point_source_count=1,
                        calibrated_magnitude=11.6,
                        calibrated_magnitude_error=0.013,
                    ),
                    PhotometryMeasurement(
                        source_id="3",
                        source_name="Check 1",
                        catalog="gaia-dr3",
                        object_name="Demo",
                        file_path=frame_b,
                        observation_time=datetime(2026, 3, 16, 2, 0, 0),
                        filter_name="CV",
                        ra_deg=10.3,
                        dec_deg=20.3,
                        x=102.0,
                        y=102.0,
                        flux=6600.0,
                        flux_error=11.0,
                        instrumental_magnitude=-9.45,
                        differential_magnitude=-0.06,
                        is_variable=False,
                        is_reference=False,
                        is_check=True,
                        flags=[],
                        catalog_magnitude=11.3,
                    ),
                    PhotometryMeasurement(
                        source_id="2",
                        source_name="Comp 1",
                        catalog="gaia-dr3",
                        object_name="Demo",
                        file_path=frame_b,
                        observation_time=datetime(2026, 3, 16, 2, 0, 0),
                        filter_name="CV",
                        ra_deg=10.2,
                        dec_deg=20.2,
                        x=101.0,
                        y=101.0,
                        flux=7050.0,
                        flux_error=10.0,
                        instrumental_magnitude=-9.7,
                        differential_magnitude=None,
                        is_variable=False,
                        is_reference=True,
                        flags=[],
                    ),
                ],
                light_curves=[],
                notes=[],
            )

            export_processing_report(
                report,
                root / "exports",
                science_metadata=ScienceExportMetadata(observer_code="KAY", aavso_chart_id="X12345ABC", filter_system="CV"),
            )

            accepted_json = json.loads((root / "exports" / "Demo_accepted_observations.json").read_text(encoding="utf-8"))
            rows = [row for row in accepted_json["rows"] if row["role"] == "variable"]
            self.assertEqual(len(rows), 2)
            self.assertEqual(rows[0]["check_source_name"], "Check 1")
            self.assertAlmostEqual(rows[0]["check_reference_magnitude"], 11.3, places=6)
            self.assertAlmostEqual(rows[0]["check_calibrated_magnitude"], 11.35, places=6)
            self.assertAlmostEqual(rows[0]["check_residual_magnitude"], 0.05, places=6)
            self.assertAlmostEqual(rows[0]["check_residual_rms"], 0.05, places=6)
            self.assertAlmostEqual(rows[1]["check_residual_magnitude"], -0.05, places=6)
            self.assertAlmostEqual(rows[1]["check_residual_rms"], 0.05, places=6)

    def test_preview_aavso_extended_export_reports_chart_and_airmass_warnings(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            measurement_path = root / "Files" / "R_Cas" / "missing.fits"
            report = ProcessingReport(
                object_name="R_Cas",
                files_processed=1,
                solved_files=1,
                field_catalog=FieldCatalog(10.0, 20.0, 0.5, variable_stars=[CatalogStar("vsx", "1", "R Cas", 10.0, 20.0, 7.0, True)]),
                total_variable_stars_found=1,
                variable_star_limit_mode=VariableStarLimitMode.PERCENT,
                variable_star_limit_value=100,
                reference_stars=[CatalogStar("gaia-dr3", "2", "Comp 1", 10.2, 20.2, 11.0, False)],
                measurements=[
                    PhotometryMeasurement(
                        source_id="1",
                        source_name="R Cas",
                        catalog="vsx",
                        object_name="R_Cas",
                        file_path=measurement_path,
                        observation_time=datetime(2026, 3, 16, 1, 0, 0),
                        filter_name="R",
                        ra_deg=10.0,
                        dec_deg=20.0,
                        x=100.0,
                        y=100.0,
                        flux=5000.0,
                        flux_error=15.0,
                        instrumental_magnitude=-9.2,
                        differential_magnitude=-0.2,
                        differential_magnitude_error=0.012,
                        is_variable=True,
                        is_reference=False,
                        comparison_source_ids=["2"],
                        comparison_source_names=["Comp 1"],
                        flags=[],
                    ),
                    PhotometryMeasurement(
                        source_id="2",
                        source_name="Comp 1",
                        catalog="gaia-dr3",
                        object_name="R_Cas",
                        file_path=measurement_path,
                        observation_time=datetime(2026, 3, 16, 1, 0, 0),
                        filter_name="R",
                        ra_deg=10.2,
                        dec_deg=20.2,
                        x=101.0,
                        y=101.0,
                        flux=7000.0,
                        flux_error=10.0,
                        instrumental_magnitude=-9.7,
                        differential_magnitude=None,
                        is_variable=False,
                        is_reference=True,
                        flags=[],
                    ),
                ],
                light_curves=[],
                notes=[],
            )

            preflight = preview_aavso_extended_export(
                report,
                science_metadata=ScienceExportMetadata(observer_code="KAY"),
            )

            self.assertFalse(preflight["chart_id_present"])
            self.assertEqual(preflight["observation_count"], 1)
            self.assertIn("AAVSO sequence/chart ID is blank; set it in Settings if you used an AAVSO sequence.", preflight["warnings"])
            self.assertIn("At least one exported row is missing airmass; no header value or computed site fallback was available.", preflight["warnings"])

    def test_export_processing_report_computes_airmass_from_site_metadata_when_header_missing(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            measurement_path = root / "Files" / "R_Cas" / "computed_airmass.fits"
            measurement_path.parent.mkdir(parents=True)
            fits.PrimaryHDU(data=np.ones((4, 4), dtype=float)).writeto(measurement_path)
            location = EarthLocation(lat=51.5074 * u.deg, lon=-0.1278 * u.deg, height=35.0 * u.m)
            observation_time = datetime(2026, 3, 16, 1, 0, 0)
            local_sidereal_time = Time(observation_time, scale="utc").sidereal_time("mean", longitude=location.lon)
            target_ra_deg = float(local_sidereal_time.degree)
            target_dec_deg = 51.5074
            report = ProcessingReport(
                object_name="R_Cas",
                files_processed=1,
                solved_files=1,
                field_catalog=FieldCatalog(10.0, 20.0, 0.5, variable_stars=[CatalogStar("vsx", "1", "R Cas", target_ra_deg, target_dec_deg, 7.0, True)]),
                total_variable_stars_found=1,
                variable_star_limit_mode=VariableStarLimitMode.PERCENT,
                variable_star_limit_value=100,
                reference_stars=[CatalogStar("gaia-dr3", "2", "Comp 1", target_ra_deg + 0.2, target_dec_deg + 0.2, 11.0, False)],
                measurements=[
                    PhotometryMeasurement(
                        source_id="1",
                        source_name="R Cas",
                        catalog="vsx",
                        object_name="R_Cas",
                        file_path=measurement_path,
                        observation_time=observation_time,
                        filter_name="R",
                        ra_deg=target_ra_deg,
                        dec_deg=target_dec_deg,
                        x=100.0,
                        y=100.0,
                        flux=5000.0,
                        flux_error=15.0,
                        instrumental_magnitude=-9.2,
                        differential_magnitude=-0.2,
                        differential_magnitude_error=0.012,
                        is_variable=True,
                        is_reference=False,
                        comparison_source_ids=["2"],
                        comparison_source_names=["Comp 1"],
                        flags=[],
                    ),
                    PhotometryMeasurement(
                        source_id="2",
                        source_name="Comp 1",
                        catalog="gaia-dr3",
                        object_name="R_Cas",
                        file_path=measurement_path,
                        observation_time=observation_time,
                        filter_name="R",
                        ra_deg=target_ra_deg + 0.2,
                        dec_deg=target_dec_deg + 0.2,
                        x=101.0,
                        y=101.0,
                        flux=7000.0,
                        flux_error=10.0,
                        instrumental_magnitude=-9.7,
                        differential_magnitude=None,
                        is_variable=False,
                        is_reference=True,
                        flags=[],
                    ),
                ],
                light_curves=[],
                notes=[],
            )

            result = export_processing_report(
                report,
                root / "exports",
                science_metadata=ScienceExportMetadata(
                    observer_code="KAY",
                    aavso_chart_id="X12345ABC",
                    observing_site_latitude_deg=51.5074,
                    observing_site_longitude_deg=-0.1278,
                    observing_site_elevation_m=35.0,
                ),
            )

            aavso_text = (root / "exports" / "R_Cas_aavso_extended.txt").read_text(encoding="ascii")
            preflight = json.loads((root / "exports" / "R_Cas_aavso_preflight.json").read_text(encoding="utf-8"))
            data_line = next(line for line in aavso_text.splitlines() if line.startswith("R Cas,"))
            amass_field = data_line.split(",")[11]

            self.assertEqual(result["aavso_observation_count"], 1)
            self.assertNotEqual(amass_field, "na")
            self.assertGreaterEqual(float(amass_field), 1.0)
            self.assertLess(float(amass_field), 1.1)
            self.assertNotIn(
                "At least one exported row is missing airmass; no header value or computed site fallback was available.",
                preflight["warnings"],
            )

    def test_plot_light_curve_applies_selected_theme_colors(self) -> None:
        figure = Figure(figsize=(4, 3))
        axis = figure.add_subplot(111)
        series = LightCurveSeries(
            object_name="R_Cas",
            source_id="1",
            source_name="R Cas",
            filter_name="R",
            points=[
                LightCurvePoint(
                    observation_time=datetime(2026, 3, 16, 1, 0, 0),
                    file_path=Path("a.fits"),
                    differential_magnitude=-0.2,
                    differential_magnitude_error=0.01,
                    instrumental_magnitude=-9.2,
                    flux=5000.0,
                    flux_error=15.0,
                ),
                LightCurvePoint(
                    observation_time=datetime(2026, 3, 16, 2, 0, 0),
                    file_path=Path("b.fits"),
                    differential_magnitude=-0.1,
                    differential_magnitude_error=0.01,
                    instrumental_magnitude=-9.1,
                    flux=5200.0,
                    flux_error=14.0,
                ),
                LightCurvePoint(
                    observation_time=datetime(2026, 3, 16, 3, 0, 0),
                    file_path=Path("c.fits"),
                    differential_magnitude=-0.15,
                    differential_magnitude_error=0.01,
                    instrumental_magnitude=-9.15,
                    flux=5100.0,
                    flux_error=13.0,
                ),
            ],
        )

        plot_light_curve(
            axis,
            series,
            "No valid values for the selected light-curve axis.",
            fit_config=LightCurveFitConfig(),
            theme="dracula",
        )

        self.assertEqual(to_hex(axis.get_facecolor(), keep_alpha=False), "#1e1f29")
        self.assertEqual(to_hex(axis.lines[-1].get_color(), keep_alpha=False), "#ffb86c")

    def test_export_processing_report_does_not_write_annotated_images(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            image_path = root / "Files" / "R_Cas" / "a.fits"
            image_path.parent.mkdir(parents=True)
            fits.PrimaryHDU(data=np.arange(64 * 64, dtype=float).reshape(64, 64)).writeto(image_path)

            variable_measurement = PhotometryMeasurement(
                source_id="1",
                source_name="R Cas",
                catalog="vsx",
                object_name="R_Cas",
                file_path=image_path,
                observation_time=datetime(2026, 3, 16, 1, 0, 0),
                filter_name="R",
                ra_deg=10.0,
                dec_deg=20.0,
                x=20.0,
                y=22.0,
                flux=5000.0,
                flux_error=15.0,
                instrumental_magnitude=-9.2,
                differential_magnitude=-0.2,
                is_variable=True,
                is_reference=False,
                flags=[],
                comparison_source_ids=["2"],
                comparison_source_names=["Comp 1"],
                aperture_radius=4.0,
                annulus_inner_radius=7.0,
                annulus_outer_radius=10.0,
            )
            reference_measurement = PhotometryMeasurement(
                source_id="2",
                source_name="Comp 1",
                catalog="gaia-dr3",
                object_name="R_Cas",
                file_path=image_path,
                observation_time=datetime(2026, 3, 16, 1, 0, 0),
                filter_name="R",
                ra_deg=10.2,
                dec_deg=20.2,
                x=34.0,
                y=18.0,
                flux=7000.0,
                flux_error=10.0,
                instrumental_magnitude=-9.7,
                differential_magnitude=None,
                is_variable=False,
                is_reference=True,
                flags=[],
                aperture_radius=4.0,
                annulus_inner_radius=7.0,
                annulus_outer_radius=10.0,
            )
            report = ProcessingReport(
                object_name="R_Cas",
                files_processed=1,
                solved_files=1,
                field_catalog=FieldCatalog(10.0, 20.0, 0.5, variable_stars=[CatalogStar("vsx", "1", "R Cas", 10.0, 20.0, 7.0, True)]),
                total_variable_stars_found=1,
                variable_star_limit_mode=VariableStarLimitMode.PERCENT,
                variable_star_limit_value=100,
                reference_stars=[CatalogStar("gaia-dr3", "2", "Comp 1", 10.2, 20.2, 11.0, False)],
                measurements=[variable_measurement, reference_measurement],
                light_curves=[
                    LightCurveSeries(
                        object_name="R_Cas",
                        source_id="1",
                        source_name="R Cas",
                        filter_name="R",
                        points=[
                            LightCurvePoint(
                                observation_time=variable_measurement.observation_time,
                                file_path=image_path,
                                differential_magnitude=-0.2,
                                instrumental_magnitude=-9.2,
                                flux=5000.0,
                                flux_error=15.0,
                            )
                        ],
                    )
                ],
            )

            result = export_processing_report(report, root / "exports")

            self.assertEqual(result["annotated_image_count"], 0)
            self.assertFalse((root / "exports" / "R_Cas_annotated_images").exists())

    def test_export_annotated_images_reuses_display_for_shared_frame(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            shared_path = root / "Files" / "Demo" / "frame_01.fits"
            shared_path.parent.mkdir(parents=True)
            fits.PrimaryHDU(data=np.ones((4, 4), dtype=float)).writeto(shared_path)
            report = ProcessingReport(
                object_name="Demo",
                files_processed=1,
                solved_files=1,
                field_catalog=FieldCatalog(10.0, 20.0, 0.5, variable_stars=[]),
                total_variable_stars_found=2,
                variable_star_limit_mode=VariableStarLimitMode.PERCENT,
                variable_star_limit_value=100,
                reference_stars=[],
                measurements=[
                    PhotometryMeasurement(
                        source_id="1",
                        source_name="Var 1",
                        catalog="vsx",
                        object_name="Demo",
                        file_path=shared_path,
                        observation_time=datetime(2026, 3, 16, 1, 0, 0),
                        filter_name="R",
                        ra_deg=10.0,
                        dec_deg=20.0,
                        x=100.0,
                        y=100.0,
                        flux=5000.0,
                        flux_error=15.0,
                        instrumental_magnitude=-9.2,
                        differential_magnitude=-0.2,
                        is_variable=True,
                        is_reference=False,
                        flags=[],
                    ),
                    PhotometryMeasurement(
                        source_id="2",
                        source_name="Var 2",
                        catalog="vsx",
                        object_name="Demo",
                        file_path=shared_path,
                        observation_time=datetime(2026, 3, 16, 1, 0, 0),
                        filter_name="R",
                        ra_deg=10.2,
                        dec_deg=20.2,
                        x=120.0,
                        y=120.0,
                        flux=4800.0,
                        flux_error=16.0,
                        instrumental_magnitude=-9.0,
                        differential_magnitude=-0.1,
                        is_variable=True,
                        is_reference=False,
                        flags=[],
                    ),
                ],
                light_curves=[],
                notes=[],
            )
            display = AnnotatedImageDisplay(
                image_path=shared_path,
                normalized_data=np.ones((4, 4), dtype=float),
                norm=None,
            )

            with patch("photometry_app.core.exporters.build_annotated_image_display", return_value=display) as build_display:
                result = export_annotated_images(report, root / "exports")

            self.assertEqual(result["annotated_image_count"], 2)
            build_display.assert_called_once_with(shared_path)

    def test_export_light_curve_plots_can_use_flux_axis(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            report = ProcessingReport(
                object_name="R_Cas",
                files_processed=1,
                solved_files=1,
                field_catalog=FieldCatalog(10.0, 20.0, 0.5, variable_stars=[CatalogStar("vsx", "1", "R Cas", 10.0, 20.0, 7.0, True)]),
                light_curves=[
                    LightCurveSeries(
                        object_name="R_Cas",
                        source_id="1",
                        source_name="R Cas",
                        filter_name="R",
                        points=[
                            LightCurvePoint(
                                observation_time=datetime(2026, 3, 16, 1, 0, 0),
                                file_path=Path("a.fits"),
                                differential_magnitude=-0.2,
                                instrumental_magnitude=-9.2,
                                flux=5000.0,
                                flux_error=15.0,
                            )
                        ],
                    )
                ],
            )

            result = export_light_curve_plots(report, root / "exports", y_axis_mode="flux")

            self.assertEqual(result["plot_count"], 1)
            self.assertTrue((root / "exports" / "R_Cas_plots" / "R_Cas_R.png").exists())

    def test_export_light_curve_animated_gif_writes_looped_gif(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            output_path = Path(temp_dir) / "animated.gif"
            series = LightCurveSeries(
                object_name="R_Cas",
                source_id="1",
                source_name="R Cas",
                filter_name="R",
                points=[
                    LightCurvePoint(
                        observation_time=datetime(2026, 3, 16, 1, 0, 0),
                        file_path=Path("a.fits"),
                        differential_magnitude=-0.2,
                        instrumental_magnitude=-9.2,
                        flux=5000.0,
                        flux_error=15.0,
                    ),
                    LightCurvePoint(
                        observation_time=datetime(2026, 3, 16, 2, 0, 0),
                        file_path=Path("b.fits"),
                        differential_magnitude=-0.1,
                        instrumental_magnitude=-9.0,
                        flux=5200.0,
                        flux_error=14.0,
                    ),
                    LightCurvePoint(
                        observation_time=datetime(2026, 3, 16, 3, 0, 0),
                        file_path=Path("c.fits"),
                        differential_magnitude=-0.3,
                        instrumental_magnitude=-9.3,
                        flux=5400.0,
                        flux_error=13.0,
                    ),
                ],
            )

            export_light_curve_animated_gif(
                series,
                output_path,
                fit_config=LightCurveFitConfig(mode="polynomial", polynomial_degree=2),
                figure_size_inches=(4.0, 2.5),
                dpi=90,
                frame_duration_ms=80,
                minimum_duration_seconds=0.8,
            )

            self.assertTrue(output_path.exists())
            with Image.open(output_path) as image:
                self.assertTrue(getattr(image, "is_animated", False))
                self.assertGreater(getattr(image, "n_frames", 1), 1)
                self.assertEqual(image.info.get("loop"), 0)

    def test_export_light_curve_animated_gif_reports_progress(self) -> None:
        series = LightCurveSeries(
            object_name="R_Cas",
            source_id="1",
            source_name="R Cas",
            filter_name="R",
            points=[
                LightCurvePoint(
                    observation_time=datetime(2026, 3, 16, 1, 0, 0),
                    file_path=Path("a.fits"),
                    differential_magnitude=-0.2,
                    instrumental_magnitude=-9.2,
                    flux=5000.0,
                    flux_error=15.0,
                ),
                LightCurvePoint(
                    observation_time=datetime(2026, 3, 16, 2, 0, 0),
                    file_path=Path("b.fits"),
                    differential_magnitude=-0.1,
                    instrumental_magnitude=-9.0,
                    flux=5200.0,
                    flux_error=14.0,
                ),
            ],
        )
        progress_events: list[tuple[int, int, str]] = []

        with tempfile.TemporaryDirectory() as temp_dir:
            output_path = Path(temp_dir) / "animated.gif"
            with patch("photometry_app.core.exporters.export_qimages_to_gif") as export_gif:
                export_light_curve_animated_gif(
                    series,
                    output_path,
                    figure_size_inches=(4.0, 2.5),
                    dpi=90,
                    frame_duration_ms=80,
                    minimum_duration_seconds=0.4,
                    progress_callback=lambda completed, total, message: progress_events.append((completed, total, message)),
                )

        export_gif.assert_called_once()
        self.assertTrue(progress_events)
        self.assertEqual(progress_events[0][2], "Preparing animated light curve...")
        self.assertTrue(any("Rendering animation frame" in message for _, _, message in progress_events))
        self.assertTrue(any(message == "Encoding animated GIF..." for _, _, message in progress_events))
        self.assertEqual(progress_events[-1][0], progress_events[-1][1])

    def test_plot_light_curve_payload_can_hide_empty_message_and_keep_grid(self) -> None:
        figure = Figure(figsize=(4.0, 2.5), dpi=90)
        axis = figure.add_subplot(111)
        payload = LightCurvePlotPayload(
            title="Demo [V]",
            y_axis_label="Magnitude",
            x_axis_label="Phase",
            x_axis_mode="phase",
            invert_y=True,
            points=(),
            fit_x_values=None,
            fit_y_values=None,
            empty_message="No light curve to show",
            x_limits=(0.0, 1.0),
        )

        plot_light_curve_payload(
            axis,
            payload,
            x_limits=(0.0, 1.0),
            y_limits=(10.0, 12.0),
            show_empty_message=False,
            grid_alpha_override=0.5,
            show_minor_grid=True,
        )

        self.assertNotIn("No light curve to show", [text.get_text() for text in axis.texts])
        figure.canvas.draw()
        visible_gridlines = [line for line in axis.get_xgridlines() + axis.get_ygridlines() if line.get_visible()]
        minor_gridlines = [
            tick.gridline
            for tick in axis.xaxis.get_minor_ticks() + axis.yaxis.get_minor_ticks()
            if tick.gridline.get_visible()
        ]
        self.assertTrue(visible_gridlines)
        self.assertTrue(minor_gridlines)
        self.assertAlmostEqual(float(visible_gridlines[0].get_alpha()), 0.5, places=2)
        self.assertLess(float(minor_gridlines[0].get_alpha()), 0.5)

    def test_export_annotated_images_exports_only_variable_rows(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            image_path = root / "Files" / "R_Cas" / "a.fits"
            image_path.parent.mkdir(parents=True)
            fits.PrimaryHDU(data=np.arange(32 * 32, dtype=float).reshape(32, 32)).writeto(image_path)
            report = ProcessingReport(
                object_name="R_Cas",
                files_processed=1,
                solved_files=1,
                field_catalog=FieldCatalog(10.0, 20.0, 0.5),
                measurements=[
                    PhotometryMeasurement(
                        source_id="1",
                        source_name="R Cas",
                        catalog="vsx",
                        object_name="R_Cas",
                        file_path=image_path,
                        observation_time=datetime(2026, 3, 16, 1, 0, 0),
                        filter_name="R",
                        ra_deg=10.0,
                        dec_deg=20.0,
                        x=15.0,
                        y=15.0,
                        flux=5000.0,
                        flux_error=15.0,
                        instrumental_magnitude=-9.2,
                        differential_magnitude=-0.2,
                        is_variable=True,
                        is_reference=False,
                        flags=[],
                    ),
                    PhotometryMeasurement(
                        source_id="2",
                        source_name="Ref",
                        catalog="gaia-dr3",
                        object_name="R_Cas",
                        file_path=image_path,
                        observation_time=datetime(2026, 3, 16, 1, 0, 0),
                        filter_name="R",
                        ra_deg=10.1,
                        dec_deg=20.1,
                        x=18.0,
                        y=18.0,
                        flux=6000.0,
                        flux_error=12.0,
                        instrumental_magnitude=-9.4,
                        differential_magnitude=None,
                        is_variable=False,
                        is_reference=True,
                        flags=[],
                    ),
                ],
            )

            result = export_annotated_images(report, root / "exports")

            self.assertEqual(result["annotated_image_count"], 1)
            self.assertTrue((root / "exports" / "R_Cas_annotated_images" / "R_Cas_R_a.png").exists())

    def test_plot_light_curve_uses_real_time_spacing_when_timestamps_exist(self) -> None:
        figure = Figure(figsize=(8, 4.5))
        axis = figure.add_subplot(111)
        series = LightCurveSeries(
            object_name="R_Cas",
            source_id="1",
            source_name="R Cas",
            filter_name="R",
            points=[
                LightCurvePoint(
                    observation_time=datetime(2026, 3, 16, 1, 0, 0),
                    file_path=Path("a.fits"),
                    differential_magnitude=-0.2,
                    differential_magnitude_error=0.01,
                    instrumental_magnitude=-9.2,
                    flux=5000.0,
                    flux_error=15.0,
                ),
                LightCurvePoint(
                    observation_time=datetime(2026, 3, 16, 1, 30, 0),
                    file_path=Path("b.fits"),
                    differential_magnitude=-0.1,
                    differential_magnitude_error=0.01,
                    instrumental_magnitude=-9.1,
                    flux=5100.0,
                    flux_error=15.0,
                ),
                LightCurvePoint(
                    observation_time=datetime(2026, 3, 17, 1, 30, 0),
                    file_path=Path("c.fits"),
                    differential_magnitude=-0.4,
                    differential_magnitude_error=0.01,
                    instrumental_magnitude=-9.4,
                    flux=5200.0,
                    flux_error=15.0,
                ),
            ],
        )

        plot_light_curve(axis, series, "No valid differential magnitudes.")

        x_values = list(axis.lines[0].get_xdata())
        self.assertEqual(len(x_values), 3)
        short_gap = x_values[1] - x_values[0]
        long_gap = x_values[2] - x_values[1]
        self.assertGreater(long_gap, short_gap * 10)
        self.assertEqual(axis.get_xlabel(), "Observation Time")
        self.assertIn(axis.lines[0].get_linestyle(), {"None", "none", ""})
        self.assertEqual(axis.lines[0].get_markersize(), 3)
        self.assertGreater(len(axis.collections), 0)

    def test_build_light_curve_plot_payload_uses_time_mode_when_timestamps_exist(self) -> None:
        series = LightCurveSeries(
            object_name="R_Cas",
            source_id="1",
            source_name="R Cas",
            filter_name="R",
            points=[
                LightCurvePoint(
                    observation_time=datetime(2026, 3, 16, 1, 0, 0),
                    file_path=Path("a.fits"),
                    differential_magnitude=-0.2,
                    differential_magnitude_error=0.01,
                    instrumental_magnitude=-9.2,
                    flux=5000.0,
                    flux_error=15.0,
                ),
                LightCurvePoint(
                    observation_time=datetime(2026, 3, 16, 3, 0, 0),
                    file_path=Path("b.fits"),
                    differential_magnitude=-0.1,
                    differential_magnitude_error=0.01,
                    instrumental_magnitude=-9.1,
                    flux=5100.0,
                    flux_error=15.0,
                ),
            ],
        )

        payload = build_light_curve_plot_payload(series, "No valid values.")

        self.assertEqual(payload.x_axis_mode, "datetime")
        self.assertEqual(payload.x_axis_label, "Observation Time")
        self.assertAlmostEqual(payload.points[1].x - payload.points[0].x, 2.0 / 24.0)

    def test_build_light_curve_plot_payload_can_use_julian_date_axis(self) -> None:
        series = LightCurveSeries(
            object_name="R_Cas",
            source_id="1",
            source_name="R Cas",
            filter_name="R",
            points=[
                LightCurvePoint(
                    observation_time=datetime(2026, 3, 16, 1, 0, 0),
                    file_path=Path("a.fits"),
                    differential_magnitude=-0.2,
                    differential_magnitude_error=0.01,
                    instrumental_magnitude=-9.2,
                    flux=5000.0,
                    flux_error=15.0,
                ),
                LightCurvePoint(
                    observation_time=datetime(2026, 3, 16, 3, 0, 0),
                    file_path=Path("b.fits"),
                    differential_magnitude=-0.1,
                    differential_magnitude_error=0.01,
                    instrumental_magnitude=-9.1,
                    flux=5100.0,
                    flux_error=15.0,
                ),
            ],
        )

        payload = build_light_curve_plot_payload(series, "No valid values.", x_axis_mode="jd")

        self.assertEqual(payload.x_axis_mode, "jd")
        self.assertEqual(payload.x_axis_label, "Julian Date (JD)")
        self.assertAlmostEqual(payload.points[1].x - payload.points[0].x, 2.0 / 24.0)

    def test_build_light_curve_plot_payload_can_phase_fold_series(self) -> None:
        series = LightCurveSeries(
            object_name="R_Cas",
            source_id="1",
            source_name="R Cas",
            filter_name="R",
            points=[
                LightCurvePoint(
                    observation_time=datetime(2026, 3, 16, 1, 0, 0),
                    file_path=Path("a.fits"),
                    differential_magnitude=-0.2,
                    differential_magnitude_error=0.01,
                    instrumental_magnitude=-9.2,
                    flux=5000.0,
                    flux_error=15.0,
                ),
                LightCurvePoint(
                    observation_time=datetime(2026, 3, 16, 2, 0, 0),
                    file_path=Path("b.fits"),
                    differential_magnitude=-0.1,
                    differential_magnitude_error=0.01,
                    instrumental_magnitude=-9.1,
                    flux=5100.0,
                    flux_error=15.0,
                ),
            ],
        )

        payload = build_light_curve_plot_payload(series, "No valid values.", x_axis_mode="phase", phase_period_hours=2.0)

        self.assertEqual(payload.x_axis_mode, "phase")
        self.assertEqual(payload.x_axis_label, "Phase")
        self.assertTrue(all(0.0 <= point.x < 1.0 for point in payload.points))

    def test_build_light_curve_plot_payload_uses_index_mode_without_timestamps(self) -> None:
        series = LightCurveSeries(
            object_name="R_Cas",
            source_id="1",
            source_name="R Cas",
            filter_name="R",
            points=[
                LightCurvePoint(
                    observation_time=None,
                    file_path=Path("a.fits"),
                    differential_magnitude=-0.2,
                    differential_magnitude_error=0.01,
                    instrumental_magnitude=-9.2,
                    flux=5000.0,
                    flux_error=15.0,
                ),
                LightCurvePoint(
                    observation_time=None,
                    file_path=Path("b.fits"),
                    differential_magnitude=-0.1,
                    differential_magnitude_error=0.01,
                    instrumental_magnitude=-9.1,
                    flux=5100.0,
                    flux_error=15.0,
                ),
            ],
        )

        payload = build_light_curve_plot_payload(series, "No valid values.")

        self.assertEqual(payload.x_axis_mode, "index")
        self.assertEqual(payload.index_labels, ("a.fits", "b.fits"))
        self.assertEqual([point.x for point in payload.points], [0.0, 1.0])

    def test_plot_light_curve_can_switch_to_flux_axis(self) -> None:
        figure = Figure(figsize=(8, 4.5))
        axis = figure.add_subplot(111)
        series = LightCurveSeries(
            object_name="R_Cas",
            source_id="1",
            source_name="R Cas",
            filter_name="R",
            points=[
                LightCurvePoint(
                    observation_time=datetime(2026, 3, 16, 1, 0, 0),
                    file_path=Path("a.fits"),
                    differential_magnitude=-0.2,
                    instrumental_magnitude=-9.2,
                    flux=5000.0,
                    flux_error=15.0,
                ),
                LightCurvePoint(
                    observation_time=datetime(2026, 3, 16, 1, 30, 0),
                    file_path=Path("b.fits"),
                    differential_magnitude=-0.1,
                    instrumental_magnitude=-9.1,
                    flux=5100.0,
                    flux_error=15.0,
                ),
            ],
        )

        plot_light_curve(axis, series, "No valid values.", y_axis_mode="flux")

        self.assertEqual(axis.get_ylabel(), "Flux (counts)")
        self.assertFalse(axis.yaxis_inverted())
        self.assertEqual(list(axis.lines[0].get_ydata()), [5000.0, 5100.0])
        self.assertGreater(len(axis.collections), 0)

    def test_plot_light_curve_can_switch_to_relative_flux_axis(self) -> None:
        figure = Figure(figsize=(8, 4.5))
        axis = figure.add_subplot(111)
        series = LightCurveSeries(
            object_name="R_Cas",
            source_id="1",
            source_name="R Cas",
            filter_name="R",
            points=[
                LightCurvePoint(
                    observation_time=datetime(2026, 3, 16, 1, 0, 0),
                    file_path=Path("a.fits"),
                    differential_magnitude=-0.2,
                    differential_magnitude_error=0.01,
                    instrumental_magnitude=-9.2,
                    flux=5000.0,
                    flux_error=15.0,
                    comparison_reference_flux=10000.0,
                ),
                LightCurvePoint(
                    observation_time=datetime(2026, 3, 16, 1, 30, 0),
                    file_path=Path("b.fits"),
                    differential_magnitude=-0.1,
                    differential_magnitude_error=0.02,
                    instrumental_magnitude=-9.1,
                    flux=5100.0,
                    flux_error=15.0,
                    comparison_reference_flux=10200.0,
                ),
            ],
        )

        plot_light_curve(axis, series, "No valid values.", y_axis_mode="relative_flux")

        self.assertEqual(axis.get_ylabel(), "Relative Flux (target/reference)")

    def test_plot_light_curve_y_limits_ignore_large_error_bars(self) -> None:
        figure = Figure(figsize=(8, 4.5))
        axis = figure.add_subplot(111)
        series = LightCurveSeries(
            object_name="W_UMa",
            source_id="1",
            source_name="W UMa",
            filter_name="L",
            points=[
                LightCurvePoint(
                    observation_time=datetime(2026, 3, 17, 3, 30, 0),
                    file_path=Path("a.fits"),
                    differential_magnitude=0.0,
                    differential_magnitude_error=0.2,
                    instrumental_magnitude=-9.0,
                    flux=1000.0,
                    flux_error=10.0,
                ),
                LightCurvePoint(
                    observation_time=datetime(2026, 3, 17, 4, 0, 0),
                    file_path=Path("b.fits"),
                    differential_magnitude=1.0,
                    differential_magnitude_error=50.0,
                    instrumental_magnitude=-8.0,
                    flux=1000.0,
                    flux_error=10.0,
                ),
            ],
        )

        plot_light_curve(axis, series, "No valid differential magnitudes.")

        lower_limit, upper_limit = axis.get_ylim()
        self.assertTrue(axis.yaxis_inverted())
        self.assertLess(abs(lower_limit - upper_limit), 5.0)

    def test_plot_light_curve_can_overlay_fitted_curve(self) -> None:
        figure = Figure(figsize=(8, 4.5))
        axis = figure.add_subplot(111)
        series = LightCurveSeries(
            object_name="R_Cas",
            source_id="1",
            source_name="R Cas",
            filter_name="R",
            points=[
                LightCurvePoint(
                    observation_time=datetime(2026, 3, 16, 1, 0, 0),
                    file_path=Path("a.fits"),
                    differential_magnitude=-0.2,
                    instrumental_magnitude=-9.2,
                    flux=5000.0,
                    flux_error=15.0,
                ),
                LightCurvePoint(
                    observation_time=datetime(2026, 3, 16, 1, 20, 0),
                    file_path=Path("b.fits"),
                    differential_magnitude=-0.1,
                    instrumental_magnitude=-9.1,
                    flux=5100.0,
                    flux_error=15.0,
                ),
                LightCurvePoint(
                    observation_time=datetime(2026, 3, 16, 1, 40, 0),
                    file_path=Path("c.fits"),
                    differential_magnitude=-0.3,
                    instrumental_magnitude=-9.3,
                    flux=4900.0,
                    flux_error=15.0,
                ),
                LightCurvePoint(
                    observation_time=datetime(2026, 3, 16, 2, 0, 0),
                    file_path=Path("d.fits"),
                    differential_magnitude=-0.15,
                    instrumental_magnitude=-9.15,
                    flux=5050.0,
                    flux_error=15.0,
                ),
            ],
        )

        plot_light_curve(
            axis,
            series,
            "No valid differential magnitudes.",
            fit_config=LightCurveFitConfig(mode="polynomial", polynomial_degree=3),
        )

        self.assertEqual(len(axis.lines), 2)
        self.assertGreater(len(axis.lines[1].get_xdata()), len(axis.lines[0].get_xdata()))

    def test_plot_light_curve_can_overlay_periodic_fit(self) -> None:
        figure = Figure(figsize=(8, 4.5))
        axis = figure.add_subplot(111)
        start_time = datetime(2026, 3, 16, 1, 0, 0)
        series = LightCurveSeries(
            object_name="R_Cas",
            source_id="1",
            source_name="R Cas",
            filter_name="R",
            points=[
                LightCurvePoint(
                    observation_time=start_time.replace(minute=0) + timedelta(minutes=20 * index),
                    file_path=Path(f"frame_{index:02d}.fits"),
                    differential_magnitude=float(-0.3 + 0.12 * np.sin((2.0 * np.pi * index) / 6.0)),
                    instrumental_magnitude=-9.0,
                    flux=5000.0,
                    flux_error=15.0,
                )
                for index in range(18)
            ],
        )

        plot_light_curve(
            axis,
            series,
            "No valid differential magnitudes.",
            fit_config=LightCurveFitConfig(mode="periodic", periodic_period_hours=2.0, periodic_harmonics=2),
        )

        self.assertEqual(len(axis.lines), 2)
        self.assertGreater(len(axis.lines[1].get_xdata()), len(axis.lines[0].get_xdata()))

    def test_plot_light_curve_can_overlay_spline_fit(self) -> None:
        figure = Figure(figsize=(8, 4.5))
        axis = figure.add_subplot(111)
        series = LightCurveSeries(
            object_name="R_Cas",
            source_id="1",
            source_name="R Cas",
            filter_name="R",
            points=[
                LightCurvePoint(
                    observation_time=datetime(2026, 3, 16, 1, 0, 0) + timedelta(minutes=20 * index),
                    file_path=Path(f"frame_{index:02d}.fits"),
                    differential_magnitude=float(-0.2 + 0.08 * np.sin(index / 2.0) + 0.03 * np.cos(index / 3.0)),
                    differential_magnitude_error=0.02,
                    instrumental_magnitude=-9.0,
                    flux=5000.0,
                    flux_error=15.0,
                )
                for index in range(10)
            ],
        )

        plot_light_curve(
            axis,
            series,
            "No valid differential magnitudes.",
            fit_config=LightCurveFitConfig(mode="spline", spline_smoothing=0.35),
        )

        self.assertEqual(len(axis.lines), 2)
        self.assertGreater(len(axis.lines[1].get_xdata()), len(axis.lines[0].get_xdata()))

    def test_plot_light_curve_can_auto_select_fit(self) -> None:
        figure = Figure(figsize=(8, 4.5))
        axis = figure.add_subplot(111)
        start_time = datetime(2026, 3, 16, 1, 0, 0)
        series = LightCurveSeries(
            object_name="R_Cas",
            source_id="1",
            source_name="R Cas",
            filter_name="R",
            points=[
                LightCurvePoint(
                    observation_time=start_time + timedelta(minutes=15 * index),
                    file_path=Path(f"frame_{index:02d}.fits"),
                    differential_magnitude=float(-0.25 + 0.15 * np.sin((2.0 * np.pi * index) / 8.0)),
                    differential_magnitude_error=0.02,
                    instrumental_magnitude=-9.0,
                    flux=5000.0,
                    flux_error=15.0,
                )
                for index in range(16)
            ],
        )

        plot_light_curve(
            axis,
            series,
            "No valid differential magnitudes.",
            fit_config=LightCurveFitConfig(mode="auto", periodic_period_hours=2.0, periodic_harmonics=2, spline_smoothing=0.3),
        )

        self.assertEqual(len(axis.lines), 2)
        self.assertGreater(len(axis.lines[1].get_xdata()), len(axis.lines[0].get_xdata()))

    def test_estimate_period_hours_returns_expected_period_for_periodic_series(self) -> None:
        start_time = datetime(2026, 3, 16, 1, 0, 0)
        true_period_hours = 2.4
        cadence_minutes = [0, 17, 39, 58, 81, 103, 124, 147, 171, 193, 216, 239, 261, 286, 309, 333, 358, 382]
        series = LightCurveSeries(
            object_name="R_Cas",
            source_id="1",
            source_name="R Cas",
            filter_name="R",
            points=[
                LightCurvePoint(
                    observation_time=start_time + timedelta(minutes=offset),
                    file_path=Path(f"frame_{index:02d}.fits"),
                    differential_magnitude=float(-0.25 + 0.18 * np.sin((2.0 * np.pi * offset) / (true_period_hours * 60.0))),
                    instrumental_magnitude=-9.0,
                    flux=5000.0,
                    flux_error=15.0,
                )
                for index, offset in enumerate(cadence_minutes)
            ],
        )

        estimated_period_hours = estimate_period_hours(series)

        self.assertIsNotNone(estimated_period_hours)
        assert estimated_period_hours is not None
        self.assertAlmostEqual(estimated_period_hours, true_period_hours, delta=0.35)

    def test_estimate_period_hours_prefers_full_cycle_over_half_period_alias(self) -> None:
        start_time = datetime(2026, 3, 16, 1, 0, 0)
        true_period_hours = 21.19
        cadence_minutes = [
            0, 18, 37, 55, 74, 91, 109, 128, 146, 165, 182, 201, 220, 238, 257, 275,
            24 * 60 + 5, 24 * 60 + 24, 24 * 60 + 43, 24 * 60 + 61, 24 * 60 + 80, 24 * 60 + 99,
            24 * 60 + 117, 24 * 60 + 136, 24 * 60 + 154, 24 * 60 + 173, 24 * 60 + 192, 24 * 60 + 210,
        ]
        points: list[LightCurvePoint] = []
        for index, offset in enumerate(cadence_minutes):
            phase = ((offset / 60.0) / true_period_hours) % 1.0
            primary_depth = 1.4 * np.exp(-0.5 * (((phase - 0.0 + 0.5) % 1.0 - 0.5) / 0.08) ** 2)
            secondary_depth = 1.0 * np.exp(-0.5 * (((phase - 0.5 + 0.5) % 1.0 - 0.5) / 0.10) ** 2)
            points.append(
                LightCurvePoint(
                    observation_time=start_time + timedelta(minutes=offset),
                    file_path=Path(f"frame_{index:02d}.fits"),
                    differential_magnitude=float(0.35 + primary_depth + secondary_depth),
                    differential_magnitude_error=0.03,
                    instrumental_magnitude=-9.0,
                    flux=5000.0,
                    flux_error=15.0,
                )
            )
        series = LightCurveSeries(
            object_name="UZPupLike",
            source_id="1",
            source_name="UZ Pup",
            filter_name="L",
            points=points,
        )

        estimated_period_hours = estimate_period_hours(series)

        self.assertIsNotNone(estimated_period_hours)
        assert estimated_period_hours is not None
        self.assertAlmostEqual(estimated_period_hours, true_period_hours, delta=1.5)

    def test_supports_periodic_auto_fit_rejects_sparse_two_window_sampling(self) -> None:
        x_array = np.asarray(
            [offset / (60.0 * 24.0) for offset in [
                0, 18, 37, 55, 74, 91, 109, 128, 146, 165, 182, 201, 220, 238, 257, 275,
                24 * 60 + 5, 24 * 60 + 24, 24 * 60 + 43, 24 * 60 + 61, 24 * 60 + 80, 24 * 60 + 99,
                24 * 60 + 117, 24 * 60 + 136, 24 * 60 + 154, 24 * 60 + 173, 24 * 60 + 192, 24 * 60 + 210,
            ]],
            dtype=float,
        )

        supports_periodic = _supports_periodic_auto_fit(
            x_array,
            LightCurveFitConfig(mode="auto", periodic_period_hours=10.77, periodic_harmonics=2),
            x_units="days",
        )

        self.assertFalse(supports_periodic)

    def test_estimate_period_hours_supports_box_least_squares(self) -> None:
        start_time = datetime(2026, 3, 16, 1, 0, 0)
        true_period_hours = 2.0
        cadence_minutes = [10 * index for index in range(48)]
        points: list[LightCurvePoint] = []
        for index, offset in enumerate(cadence_minutes):
            phase = ((offset / 60.0) / true_period_hours) % 1.0
            value = 0.45 if phase < 0.12 else 0.01
            points.append(
                LightCurvePoint(
                    observation_time=start_time + timedelta(minutes=offset),
                    file_path=Path(f"frame_{index:02d}.fits"),
                    differential_magnitude=value,
                    differential_magnitude_error=0.02,
                    instrumental_magnitude=-9.0,
                    flux=5000.0,
                    flux_error=15.0,
                )
            )
        series = LightCurveSeries(
            object_name="TransitCandidate",
            source_id="1",
            source_name="TransitCandidate",
            filter_name="R",
            points=points,
        )

        estimated_period_hours = estimate_period_hours(series, method="bls")

        self.assertIsNotNone(estimated_period_hours)
        assert estimated_period_hours is not None
        self.assertAlmostEqual(estimated_period_hours, true_period_hours, delta=0.5)

    def test_infer_fit_period_hours_returns_expected_period_for_periodic_series(self) -> None:
        start_time = datetime(2026, 3, 16, 1, 0, 0)
        true_period_hours = 2.4
        cadence_minutes = [0, 17, 39, 58, 81, 103, 124, 147, 171, 193, 216, 239, 261, 286, 309, 333, 358, 382]
        series = LightCurveSeries(
            object_name="R_Cas",
            source_id="1",
            source_name="R Cas",
            filter_name="R",
            points=[
                LightCurvePoint(
                    observation_time=start_time + timedelta(minutes=offset),
                    file_path=Path(f"frame_{index:02d}.fits"),
                    differential_magnitude=float(-0.25 + 0.18 * np.sin((2.0 * np.pi * offset) / (true_period_hours * 60.0))),
                    differential_magnitude_error=0.02,
                    instrumental_magnitude=-9.0,
                    flux=5000.0,
                    flux_error=15.0,
                )
                for index, offset in enumerate(cadence_minutes)
            ],
        )

        estimated_period_hours = infer_fit_period_hours(
            series,
            fit_config=LightCurveFitConfig(mode="periodic", periodic_harmonics=2),
        )

        self.assertIsNotNone(estimated_period_hours)
        assert estimated_period_hours is not None
        self.assertAlmostEqual(estimated_period_hours, true_period_hours, delta=0.25)

    def test_infer_fit_period_result_rejects_search_boundary_alias(self) -> None:
        start_time = datetime(2026, 5, 9, 23, 41, 48)
        cadence_minutes = [index * 2.424 for index in range(100)]
        differential_magnitudes = [
            2.06677, 2.05144, 1.97542, 1.99513, 1.93303, 1.98321, 1.99549, 1.90966, 1.97213, 2.02654,
            1.98401, 2.00679, 1.95428, 1.92671, 1.97875, 1.92444, 1.93088, 1.92878, 1.85697, 1.92444,
            1.88756, 1.83094, 1.86686, 1.82433, 1.83685, 1.84902, 1.81872, 1.79452, 1.83632, 1.79793,
            1.79756, 1.76820, 1.75961, 1.79802, 1.75506, 1.75006, 1.74035, 1.78988, 1.81515, 1.77146,
            1.78687, 1.77977, 1.81452, 1.78593, 1.76643, 1.80198, 1.81589, 1.78280, 1.77776, 1.75536,
            1.81691, 1.81075, 1.86261, 1.83707, 1.79773, 1.82887, 1.87355, 1.85344, 1.81458, 1.81559,
            1.85681, 1.88537, 1.88808, 1.86757, 1.85422, 1.86737, 1.88014, 1.91794, 1.86986, 1.90366,
            1.89897, 1.90417, 1.95269, 1.94040, 1.92635, 1.91169, 1.95421, 1.95113, 1.93724, 1.94154,
            1.95257, 1.97037, 1.95602, 1.94406, 1.95007, 2.01238, 1.99185, 1.97898, 1.99835, 2.03026,
            2.02673, 2.06570, 2.02038, 2.07075, 2.00248, 2.03356, 2.06703, 1.96788, 2.07580, 2.08400,
        ]
        series = LightCurveSeries(
            object_name="DavidaLike",
            source_id="edge-fit",
            source_name="Edge Fit",
            filter_name="L",
            points=[
                LightCurvePoint(
                    observation_time=start_time + timedelta(minutes=offset),
                    file_path=Path(f"frame_{index:03d}.fits"),
                    differential_magnitude=float(differential_magnitudes[index]),
                    differential_magnitude_error=0.02,
                    instrumental_magnitude=-9.0,
                    flux=5000.0,
                    flux_error=15.0,
                )
                for index, offset in enumerate(cadence_minutes)
            ],
        )

        fit_result = infer_fit_period_result(
            series,
            fit_config=LightCurveFitConfig(mode="periodic", periodic_harmonics=2),
        )

        self.assertIsNone(fit_result)

        search_ceiling_days = ((cadence_minutes[-1] - cadence_minutes[0]) / (60.0 * 24.0)) * 0.95
        cached_edge_result = FitPeriodInferenceResult(period_hours=search_ceiling_days * 24.0, method="harmonic_fit")
        self.assertTrue(period_result_hits_search_edge(series, cached_edge_result))

    def test_infer_fit_period_result_reports_winning_harmonics(self) -> None:
        start_time = datetime(2026, 3, 16, 1, 0, 0)
        true_period_hours = 21.19
        cadence_minutes = [
            0, 18, 37, 55, 74, 91, 109, 128, 146, 165, 182, 201, 220, 238, 257, 275,
            24 * 60 + 5, 24 * 60 + 24, 24 * 60 + 43, 24 * 60 + 61, 24 * 60 + 80, 24 * 60 + 99,
            24 * 60 + 117, 24 * 60 + 136, 24 * 60 + 154, 24 * 60 + 173, 24 * 60 + 192, 24 * 60 + 210,
        ]
        points: list[LightCurvePoint] = []
        for index, offset in enumerate(cadence_minutes):
            phase = ((offset / 60.0) / true_period_hours) % 1.0
            primary_depth = 1.4 * np.exp(-0.5 * (((phase - 0.0 + 0.5) % 1.0 - 0.5) / 0.08) ** 2)
            secondary_depth = 1.0 * np.exp(-0.5 * (((phase - 0.5 + 0.5) % 1.0 - 0.5) / 0.10) ** 2)
            points.append(
                LightCurvePoint(
                    observation_time=start_time + timedelta(minutes=offset),
                    file_path=Path(f"frame_{index:02d}.fits"),
                    differential_magnitude=float(0.35 + primary_depth + secondary_depth),
                    differential_magnitude_error=0.03,
                    instrumental_magnitude=-9.0,
                    flux=5000.0,
                    flux_error=15.0,
                )
            )
        series = LightCurveSeries(
            object_name="UZPupLike",
            source_id="1",
            source_name="UZ Pup",
            filter_name="L",
            points=points,
        )

        fit_result = infer_fit_period_result(
            series,
            fit_config=LightCurveFitConfig(mode="periodic", periodic_harmonics=2),
        )

        self.assertIsNotNone(fit_result)
        assert fit_result is not None
        self.assertAlmostEqual(fit_result.period_hours, true_period_hours, delta=1.2)
        self.assertEqual(fit_result.periodic_harmonics, 4)

    def test_infer_fit_period_result_bls_reports_eclipse_duration(self) -> None:
        start_time = datetime(2026, 3, 16, 1, 0, 0)
        true_period_hours = 2.0
        true_duration_hours = true_period_hours * 0.12
        cadence_minutes = [10 * index for index in range(48)]
        points: list[LightCurvePoint] = []
        for index, offset in enumerate(cadence_minutes):
            phase = ((offset / 60.0) / true_period_hours) % 1.0
            value = 0.45 if phase < 0.12 else 0.01
            points.append(
                LightCurvePoint(
                    observation_time=start_time + timedelta(minutes=offset),
                    file_path=Path(f"frame_{index:02d}.fits"),
                    differential_magnitude=value,
                    differential_magnitude_error=0.02,
                    instrumental_magnitude=-9.0,
                    flux=5000.0,
                    flux_error=15.0,
                )
            )
        series = LightCurveSeries(
            object_name="TransitCandidate",
            source_id="1",
            source_name="TransitCandidate",
            filter_name="R",
            points=points,
        )

        fit_result = infer_fit_period_result(series, method="bls")

        self.assertIsNotNone(fit_result)
        assert fit_result is not None
        self.assertAlmostEqual(fit_result.period_hours, true_period_hours, delta=0.5)
        self.assertIsNotNone(fit_result.eclipse_duration_hours)
        assert fit_result.eclipse_duration_hours is not None
        self.assertAlmostEqual(fit_result.eclipse_duration_hours, true_duration_hours, delta=0.2)

    def test_infer_fit_period_hours_prefers_full_cycle_for_eclipsing_series(self) -> None:
        start_time = datetime(2026, 3, 16, 1, 0, 0)
        true_period_hours = 21.19
        cadence_minutes = [
            0, 18, 37, 55, 74, 91, 109, 128, 146, 165, 182, 201, 220, 238, 257, 275,
            24 * 60 + 5, 24 * 60 + 24, 24 * 60 + 43, 24 * 60 + 61, 24 * 60 + 80, 24 * 60 + 99,
            24 * 60 + 117, 24 * 60 + 136, 24 * 60 + 154, 24 * 60 + 173, 24 * 60 + 192, 24 * 60 + 210,
        ]
        points: list[LightCurvePoint] = []
        for index, offset in enumerate(cadence_minutes):
            phase = ((offset / 60.0) / true_period_hours) % 1.0
            primary_depth = 1.4 * np.exp(-0.5 * (((phase - 0.0 + 0.5) % 1.0 - 0.5) / 0.08) ** 2)
            secondary_depth = 1.0 * np.exp(-0.5 * (((phase - 0.5 + 0.5) % 1.0 - 0.5) / 0.10) ** 2)
            points.append(
                LightCurvePoint(
                    observation_time=start_time + timedelta(minutes=offset),
                    file_path=Path(f"frame_{index:02d}.fits"),
                    differential_magnitude=float(0.35 + primary_depth + secondary_depth),
                    differential_magnitude_error=0.03,
                    instrumental_magnitude=-9.0,
                    flux=5000.0,
                    flux_error=15.0,
                )
            )
        series = LightCurveSeries(
            object_name="UZPupLike",
            source_id="1",
            source_name="UZ Pup",
            filter_name="L",
            points=points,
        )

        estimated_period_hours = infer_fit_period_hours(
            series,
            fit_config=LightCurveFitConfig(mode="periodic", periodic_harmonics=3),
        )

        self.assertIsNotNone(estimated_period_hours)
        assert estimated_period_hours is not None
        self.assertAlmostEqual(estimated_period_hours, true_period_hours, delta=1.2)

    def test_infer_fit_period_hours_eclipsing_binary_convention_prefers_doubled_period(self) -> None:
        start_time = datetime(2026, 3, 16, 1, 0, 0)
        orbital_period_hours = 10.0
        cadence_minutes = [20 * index for index in range(72)]
        points: list[LightCurvePoint] = []
        for index, offset in enumerate(cadence_minutes):
            phase = ((offset / 60.0) / orbital_period_hours) % 1.0
            primary_depth = 1.0 * np.exp(-0.5 * (((phase - 0.0 + 0.5) % 1.0 - 0.5) / 0.07) ** 2)
            secondary_depth = 1.0 * np.exp(-0.5 * (((phase - 0.5 + 0.5) % 1.0 - 0.5) / 0.07) ** 2)
            points.append(
                LightCurvePoint(
                    observation_time=start_time + timedelta(minutes=offset),
                    file_path=Path(f"frame_{index:02d}.fits"),
                    differential_magnitude=float(0.25 + primary_depth + secondary_depth),
                    differential_magnitude_error=0.02,
                    instrumental_magnitude=-9.0,
                    flux=5000.0,
                    flux_error=15.0,
                )
            )
        series = LightCurveSeries(
            object_name="ContactBinaryLike",
            source_id="1",
            source_name="Contact Binary",
            filter_name="V",
            points=points,
        )

        standard_period_hours = infer_fit_period_hours(
            series,
            fit_config=LightCurveFitConfig(mode="periodic", periodic_harmonics=2),
            period_convention="standard",
        )
        eclipsing_binary_period_hours = infer_fit_period_hours(
            series,
            fit_config=LightCurveFitConfig(mode="periodic", periodic_harmonics=2),
            period_convention="eclipsing_binary",
        )

        self.assertIsNotNone(standard_period_hours)
        self.assertIsNotNone(eclipsing_binary_period_hours)
        assert standard_period_hours is not None
        assert eclipsing_binary_period_hours is not None
        self.assertAlmostEqual(standard_period_hours, orbital_period_hours / 2.0, delta=0.75)
        self.assertAlmostEqual(eclipsing_binary_period_hours, orbital_period_hours, delta=1.0)
