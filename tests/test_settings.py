from __future__ import annotations



import json

import os

import tempfile

import unittest

from pathlib import Path



from photometry_app.core.models import (

    AppMode,

    AperturePreset,

    ManualPhotometryConfig,

    ManualSourceConfig,

    ManualSourceRole,

    ObjectPhotometryMode,

    PhotometryApertureMode,

    RecenterMode,

    VariableStarDesignationFamily,

    VariableStarLimitMode,

)

from photometry_app.core.settings import AppSettings, ObservingSitePreset, load_settings_config_override, save_settings_config_override





class SettingsTest(unittest.TestCase):

    def setUp(self) -> None:

        self._config_dir = tempfile.TemporaryDirectory()

        self._state_dir = tempfile.TemporaryDirectory()

        self._previous_config_path = os.environ.get("CITIZEN_PHOTOMETRY_CONFIG_PATH")

        self._previous_state_path = os.environ.get("CITIZEN_PHOTOMETRY_STATE_PATH")

        os.environ["CITIZEN_PHOTOMETRY_CONFIG_PATH"] = str(Path(self._config_dir.name) / "settings.json")

        os.environ["CITIZEN_PHOTOMETRY_STATE_PATH"] = str(Path(self._state_dir.name) / "state.json")



    def tearDown(self) -> None:

        if self._previous_config_path is None:

            os.environ.pop("CITIZEN_PHOTOMETRY_CONFIG_PATH", None)

        else:

            os.environ["CITIZEN_PHOTOMETRY_CONFIG_PATH"] = self._previous_config_path

        if self._previous_state_path is None:

            os.environ.pop("CITIZEN_PHOTOMETRY_STATE_PATH", None)

        else:

            os.environ["CITIZEN_PHOTOMETRY_STATE_PATH"] = self._previous_state_path

        self._config_dir.cleanup()

        self._state_dir.cleanup()



    def test_settings_default_theme_uses_last_saved_app_theme(self) -> None:

        with tempfile.TemporaryDirectory() as temp_dir:

            root = Path(temp_dir) / "workspace"

            root.mkdir()

            state_path = Path(temp_dir) / "state.json"

            previous_state_path = os.environ.get("CITIZEN_PHOTOMETRY_STATE_PATH")

            os.environ["CITIZEN_PHOTOMETRY_STATE_PATH"] = str(state_path)

            try:

                from photometry_app.core.settings import save_last_theme



                save_last_theme("catppuccin")

                loaded = AppSettings.from_root(root)

            finally:

                if previous_state_path is None:

                    os.environ.pop("CITIZEN_PHOTOMETRY_STATE_PATH", None)

                else:

                    os.environ["CITIZEN_PHOTOMETRY_STATE_PATH"] = previous_state_path

            self.assertEqual(loaded.theme, "catppuccin")

    def test_settings_round_trip_stf_image_display_stretch_mode(self) -> None:

        with tempfile.TemporaryDirectory() as temp_dir:

            root = Path(temp_dir) / "workspace"

            root.mkdir()



            settings = AppSettings.from_root(root)

            settings.image_display_stretch_mode = "stf"

            settings.save(root)



            loaded = AppSettings.from_root(root)



        self.assertEqual(loaded.image_display_stretch_mode, "stf")

    def test_legacy_asinh_image_display_default_migrates_to_auto_stretch(self) -> None:

        with tempfile.TemporaryDirectory() as temp_dir:

            root = Path(temp_dir) / "workspace"

            root.mkdir()

            config_path = Path(os.environ["CITIZEN_PHOTOMETRY_CONFIG_PATH"])

            config_path.write_text(json.dumps({"image_display_stretch_mode": "asinh"}), encoding="utf-8")



            loaded = AppSettings.from_root(root)



        self.assertEqual(loaded.image_display_stretch_mode, "stf")

        self.assertTrue(loaded.image_display_auto_stretch_default_migrated)

    def test_settings_json_with_utf8_bom_loads(self) -> None:

        with tempfile.TemporaryDirectory() as temp_dir:

            root = Path(temp_dir) / "workspace"

            root.mkdir()

            config_path = Path(os.environ["CITIZEN_PHOTOMETRY_CONFIG_PATH"])

            config_path.write_text(json.dumps({"theme": "nord"}), encoding="utf-8-sig")



            loaded = AppSettings.from_root(root)



        self.assertEqual(loaded.theme, "nord")

    def test_explicit_asinh_selection_persists_after_auto_stretch_migration(self) -> None:

        with tempfile.TemporaryDirectory() as temp_dir:

            root = Path(temp_dir) / "workspace"

            root.mkdir()



            settings = AppSettings.from_root(root)

            settings.image_display_stretch_mode = "asinh"

            settings.save(root)

            loaded = AppSettings.from_root(root)



        self.assertEqual(loaded.image_display_stretch_mode, "asinh")



    def test_settings_default_theme_is_gruvbox_when_not_configured(self) -> None:

        with tempfile.TemporaryDirectory() as temp_dir:

            root = Path(temp_dir)

            state_path = Path(temp_dir) / "state.json"

            previous_state_path = os.environ.get("CITIZEN_PHOTOMETRY_STATE_PATH")

            os.environ["CITIZEN_PHOTOMETRY_STATE_PATH"] = str(state_path)

            try:

                loaded = AppSettings.from_root(root)

            finally:

                if previous_state_path is None:

                    os.environ.pop("CITIZEN_PHOTOMETRY_STATE_PATH", None)

                else:

                    os.environ["CITIZEN_PHOTOMETRY_STATE_PATH"] = previous_state_path



            self.assertEqual(loaded.theme, "gruvbox")

            self.assertIsInstance(loaded.custom_theme_colors, dict)

            self.assertIn("accent", loaded.custom_theme_colors or {})

            self.assertIn("plot_points", loaded.custom_theme_colors or {})

            self.assertIn("plot_fit", loaded.custom_theme_colors or {})

            self.assertIn("ra_grid", loaded.custom_theme_colors or {})

            self.assertIn("dec_grid", loaded.custom_theme_colors or {})

            self.assertIn("asteroid_other_overlay_circle_color", loaded.custom_theme_colors or {})

            self.assertIn("asteroid_other_overlay_line_width", loaded.custom_theme_colors or {})

            self.assertIn("asteroid_overlay_circle_color", loaded.custom_theme_colors or {})

            self.assertIn("asteroid_overlay_line_width", loaded.custom_theme_colors or {})

            self.assertEqual(loaded.frame_edge_margin_percent, 5.0)

            self.assertTrue(loaded.image_frame_margin_enabled)

            self.assertEqual(loaded.equatorial_grid_ra_density, 5)

            self.assertEqual(loaded.equatorial_grid_dec_density, 5)

            self.assertFalse(loaded.image_equatorial_grid_enabled)

            self.assertTrue(loaded.image_mark_saturated_enabled)

            self.assertEqual(loaded.image_display_stretch_mode, "stf")

            self.assertEqual(loaded.image_display_brightness, 0.0)

            self.assertEqual(loaded.image_display_contrast, 1.0)

            self.assertFalse(loaded.image_display_inverted)

            self.assertTrue(loaded.asteroid_visual_show_known_objects)

            self.assertTrue(loaded.asteroid_visual_show_object_markers)

            self.assertTrue(loaded.asteroid_visual_show_potential_discoveries)

            self.assertTrue(loaded.asteroid_visual_label_all_objects)

            self.assertTrue(loaded.asteroid_visual_show_all_crosshairs)

            self.assertTrue(loaded.asteroid_visual_highlight_selected_object)

            self.assertTrue(loaded.asteroid_visual_invert_annotation_colors)

            self.assertEqual(loaded.asteroid_search_parallel_workers, 0)

            self.assertEqual(loaded.asteroid_discovery_min_residual_snr, 0.0)

            self.assertEqual(loaded.asteroid_discovery_max_residual_snr, 0.0)

            self.assertEqual(loaded.asteroid_discovery_frames_per_batch, 0)

            self.assertEqual(loaded.asteroid_discovery_binning_factor, 1)

            self.assertFalse(loaded.asteroid_discovery_use_temporary_cache)

            self.assertFalse(loaded.asteroid_discovery_assume_aligned)

            self.assertFalse(loaded.asteroid_discovery_single_batch_only)

            self.assertEqual(loaded.asteroid_discovery_min_seed_displacement_px, 1.5)

            self.assertEqual(loaded.asteroid_discovery_motion_prior_bias, "balanced")

            self.assertFalse(loaded.asteroid_discovery_retry_with_detailed_search)

            self.assertEqual(loaded.asteroid_discovery_min_candidate_frames, 3)

            self.assertEqual(loaded.asteroid_discovery_detection_sigma, 5.0)

            self.assertEqual(loaded.asteroid_discovery_detection_fwhm, 3.0)

            self.assertEqual(loaded.asteroid_discovery_max_residuals_per_frame, 24)

            self.assertEqual(loaded.asteroid_discovery_edge_margin_px, 6)

            self.assertEqual(loaded.asteroid_discovery_detector_mode, "hybrid")

            self.assertEqual(loaded.asteroid_discovery_streak_min_area_px, 6)

            self.assertEqual(loaded.asteroid_discovery_streak_min_elongation, 1.8)

            self.assertEqual(loaded.asteroid_discovery_potential_deflection_rms_px, 0.9)

            self.assertEqual(loaded.asteroid_discovery_review_deflection_rms_px, 1.8)

            self.assertFalse(loaded.asteroid_discovery_enable_synthetic_sweep)

            self.assertEqual(loaded.asteroid_discovery_synthetic_sweep_max_motion_px_per_hour, 12.0)

            self.assertEqual(loaded.asteroid_discovery_synthetic_sweep_motion_step_px_per_hour, 1.0)

            self.assertEqual(loaded.asteroid_discovery_synthetic_sweep_angle_step_deg, 30.0)

            self.assertEqual(loaded.asteroid_discovery_synthetic_sweep_direction_focus, "all_directions")

            self.assertEqual(loaded.asteroid_discovery_synthetic_sweep_direction_focus_half_width_deg, 45.0)

            self.assertEqual(loaded.asteroid_discovery_synthetic_sweep_min_stacked_snr, 6.0)

            self.assertFalse(loaded.asteroid_discovery_synthetic_sweep_save_stacks)

            self.assertIsNone(loaded.differential_analysis_splitter_sizes)

            self.assertIsNone(loaded.asteroid_main_splitter_sizes)

            self.assertIsNone(loaded.asteroid_results_splitter_sizes)

            self.assertEqual(loaded.asteroid_blink_frame_duration_ms, 50)

            self.assertEqual(loaded.asteroid_gif_export_scale_percent, 100)

            self.assertTrue(loaded.asteroid_gif_export_loop_forever)

            self.assertFalse(loaded.asteroid_manual_magnitude_limit_override_enabled)

            self.assertEqual(loaded.asteroid_manual_magnitude_limit_override, 18.0)

            self.assertEqual(loaded.synthetic_tracking_crop_radius_pixels, 24)

            self.assertEqual(loaded.synthetic_tracking_integration_mode, "average")

            self.assertEqual(loaded.synthetic_tracking_weight_mode, "psf_signal_weight")

            self.assertEqual(loaded.synthetic_tracking_rejection_mode, "no_rejection")

            self.assertEqual(loaded.synthetic_tracking_backend_preference, "auto")

            self.assertEqual(loaded.synthetic_tracking_combine_mode, "mean")

            self.assertFalse(loaded.synthetic_tracking_allow_mixed_all_group)

            self.assertFalse(loaded.synthetic_tracking_advanced_enabled)

            self.assertEqual(loaded.asteroid_track_object_position_mode, "predicted")

            self.assertEqual(loaded.shared_parallel_workers, 0)

            self.assertEqual(loaded.astrostack_parallel_workers, 0)

            self.assertIsNone(loaded.telescope_focal_length_mm)

            self.assertIsNone(loaded.telescope_aperture_mm)

            self.assertIsNone(loaded.telescope_focal_ratio)

            self.assertIsNone(loaded.camera_pixel_size_um)

            self.assertIsNone(loaded.bortle_scale)

            self.assertEqual(loaded.hr_plot_color_saturation, 1.0)

            self.assertEqual(loaded.hr_plot_point_opacity, 0.8)

            self.assertEqual(loaded.hr_plot_marker_size_mode, "scaled")

            self.assertEqual(loaded.hr_plot_fixed_marker_size, 8.0)

            self.assertTrue(loaded.hr_plot_require_parallax)

            self.assertEqual(loaded.hr_table_row_limit, 1000)

            self.assertEqual(loaded.hr_motion_vector_color, "#3d8bfd")

            self.assertEqual(loaded.hr_search_catalog_names_magnitude_threshold, 9.0)

            self.assertEqual(loaded.hr_roi_drag_color, "#ff9f1c")

            self.assertEqual(loaded.hr_roi_color, "#2dd4bf")

            self.assertFalse(loaded.hr_motion_vector_color_by_angle)

            self.assertFalse(loaded.hr_motion_vector_saturation_by_magnitude)

            self.assertEqual(loaded.hr_motion_vector_width, 1.5)

            self.assertEqual(loaded.time_standard, "UTC")

            self.assertFalse(loaded.transformed)

            self.assertTrue(loaded.light_curve_scientific_export_enabled)

            self.assertEqual(loaded.scientific_light_curve_pdf_dpi, 300)

            self.assertEqual(loaded.scientific_light_curve_pdf_paper_size, "Letter")

            self.assertEqual(loaded.snr_binning_max_period_fraction, 0.03)

            self.assertEqual(loaded.snr_binning_max_absolute_duration_seconds, 600.0)

            self.assertEqual(loaded.snr_binning_target_snr, 30.0)

            self.assertEqual(loaded.snr_binning_max_frames_per_bin, 15)

            self.assertEqual(loaded.snr_binning_min_frames_per_bin, 1)

            self.assertTrue(loaded.snr_binning_type_aware_thresholds)

            self.assertEqual(loaded.snr_binning_dataset_mode, "derived")

            self.assertFalse(loaded.snr_binning_apply_to_selected_measurements_only)

            self.assertFalse(loaded.snr_binning_allow_periodless_fallback)

            self.assertEqual(loaded.discovery_max_candidate_count, 60)

            self.assertEqual(loaded.discovery_min_magnitude, 10.0)

            self.assertEqual(loaded.discovery_max_magnitude, 15.5)

            self.assertEqual(loaded.discovery_min_candidate_score, 25.0)

            self.assertEqual(loaded.sky_explorer_simbad_search_radius_arcsec, 10.0)

            self.assertEqual(loaded.sky_explorer_gaia_max_magnitude, 17.0)

            self.assertFalse(loaded.sky_explorer_gaia_hard_cap_enabled)

            self.assertEqual(loaded.sky_explorer_gaia_hard_cap_rows, 1000)

            self.assertEqual(

                loaded.sky_explorer_enabled_layers,

                ("deep_sky", "general_objects", "solar_system", "variable_stars", "gaia_stars", "exoplanets"),

            )

            self.assertEqual(loaded.sky_explorer_fill_opacity, 0.25)

            self.assertEqual(loaded.sky_explorer_stroke_opacity, 1.0)

            self.assertEqual(loaded.sky_explorer_object_group_color_overrides, {})

            self.assertEqual(loaded.sky_explorer_object_type_color_overrides, {})

            self.assertEqual(loaded.sky_explorer_object_type_text_color_overrides, {})

            self.assertEqual(loaded.sky_explorer_object_type_font_overrides, {})



    def test_defaults_return_immutable_product_defaults(self) -> None:

        with tempfile.TemporaryDirectory() as temp_dir:

            root = Path(temp_dir)

            settings = AppSettings.from_root(root)

            settings.theme = "catppuccin"

            settings.frame_edge_margin_percent = 12.5

            settings.save(root)



            defaults = AppSettings.defaults(root)



            self.assertEqual(defaults.theme, "gruvbox")

            self.assertEqual(defaults.frame_edge_margin_percent, 5.0)

            self.assertTrue(defaults.image_frame_margin_enabled)

            self.assertTrue(defaults.image_mark_saturated_enabled)



    def test_save_persists_default_settings_snapshot(self) -> None:

        with tempfile.TemporaryDirectory() as temp_dir:

            root = Path(temp_dir)

            settings = AppSettings.from_root(root)

            settings.nearby_reference_count = 9

            settings.save(root)



            payload = json.loads(Path(os.environ["CITIZEN_PHOTOMETRY_CONFIG_PATH"]).read_text(encoding="utf-8"))



            self.assertIn("default_settings", payload)

            self.assertEqual(payload["default_settings"]["frame_edge_margin_percent"], 5.0)

            self.assertEqual(payload["default_settings"]["theme"], "gruvbox")

            self.assertTrue(payload["default_settings"]["image_frame_margin_enabled"])

            self.assertTrue(payload["default_settings"]["image_mark_saturated_enabled"])

    def test_asteroid_discovery_settings_round_trip(self) -> None:

        with tempfile.TemporaryDirectory() as temp_dir:

            root = Path(temp_dir)

            settings = AppSettings.from_root(root)

            settings.asteroid_search_parallel_workers = 6

            settings.asteroid_discovery_min_residual_snr = 7.5

            settings.asteroid_discovery_max_residual_snr = 25.0

            settings.asteroid_discovery_frames_per_batch = 9

            settings.asteroid_discovery_assume_aligned = True

            settings.asteroid_discovery_single_batch_only = True

            settings.asteroid_discovery_min_seed_displacement_px = 0.65

            settings.asteroid_discovery_motion_prior_bias = "near_earth"

            settings.asteroid_discovery_retry_with_detailed_search = True

            settings.asteroid_discovery_binning_factor = 3

            settings.asteroid_discovery_use_temporary_cache = False

            settings.asteroid_discovery_min_candidate_frames = 2

            settings.asteroid_discovery_detection_sigma = 4.5

            settings.asteroid_discovery_detection_fwhm = 2.6

            settings.asteroid_discovery_max_residuals_per_frame = 41

            settings.asteroid_discovery_edge_margin_px = 3

            settings.asteroid_discovery_detector_mode = "streak"

            settings.asteroid_discovery_streak_min_area_px = 11

            settings.asteroid_discovery_streak_min_elongation = 2.4

            settings.asteroid_discovery_potential_deflection_rms_px = 0.7

            settings.asteroid_discovery_review_deflection_rms_px = 1.6

            settings.asteroid_discovery_enable_synthetic_sweep = True

            settings.asteroid_discovery_synthetic_sweep_max_motion_px_per_hour = 18.0

            settings.asteroid_discovery_synthetic_sweep_motion_step_px_per_hour = 0.8

            settings.asteroid_discovery_synthetic_sweep_angle_step_deg = 15.0

            settings.asteroid_discovery_synthetic_sweep_direction_focus = "main_belt"

            settings.asteroid_discovery_synthetic_sweep_direction_focus_half_width_deg = 22.5

            settings.asteroid_discovery_synthetic_sweep_min_stacked_snr = 7.5

            settings.asteroid_discovery_synthetic_sweep_save_stacks = True

            settings.asteroid_main_splitter_sizes = [730, 990]

            settings.asteroid_results_splitter_sizes = [470, 230]

            settings.synthetic_tracking_advanced_enabled = True

            settings.synthetic_tracking_backend_preference = "gpu"

            settings.asteroid_track_object_position_mode = "measured"

            settings.save(root)

            loaded = AppSettings.from_root(root)

        self.assertEqual(loaded.asteroid_search_parallel_workers, 6)

        self.assertEqual(loaded.asteroid_discovery_min_residual_snr, 7.5)

        self.assertEqual(loaded.asteroid_discovery_max_residual_snr, 25.0)

        self.assertEqual(loaded.asteroid_discovery_frames_per_batch, 9)

        self.assertTrue(loaded.asteroid_discovery_assume_aligned)

        self.assertTrue(loaded.asteroid_discovery_single_batch_only)

        self.assertEqual(loaded.asteroid_discovery_min_seed_displacement_px, 0.65)

        self.assertEqual(loaded.asteroid_discovery_motion_prior_bias, "near_earth")

        self.assertTrue(loaded.asteroid_discovery_retry_with_detailed_search)

        self.assertEqual(loaded.asteroid_discovery_binning_factor, 3)

        self.assertFalse(loaded.asteroid_discovery_use_temporary_cache)

        self.assertEqual(loaded.asteroid_discovery_min_candidate_frames, 2)

        self.assertEqual(loaded.asteroid_discovery_detection_sigma, 4.5)

        self.assertEqual(loaded.asteroid_discovery_detection_fwhm, 2.6)

        self.assertEqual(loaded.asteroid_discovery_max_residuals_per_frame, 41)

        self.assertEqual(loaded.asteroid_discovery_edge_margin_px, 3)

        self.assertEqual(loaded.asteroid_discovery_detector_mode, "streak")

        self.assertEqual(loaded.asteroid_discovery_streak_min_area_px, 11)

        self.assertEqual(loaded.asteroid_discovery_streak_min_elongation, 2.4)

        self.assertEqual(loaded.asteroid_discovery_potential_deflection_rms_px, 0.7)

        self.assertEqual(loaded.asteroid_discovery_review_deflection_rms_px, 1.6)

        self.assertTrue(loaded.asteroid_discovery_enable_synthetic_sweep)

        self.assertEqual(loaded.asteroid_discovery_synthetic_sweep_max_motion_px_per_hour, 18.0)

        self.assertEqual(loaded.asteroid_discovery_synthetic_sweep_motion_step_px_per_hour, 0.8)

        self.assertEqual(loaded.asteroid_discovery_synthetic_sweep_angle_step_deg, 15.0)

        self.assertEqual(loaded.asteroid_discovery_synthetic_sweep_direction_focus, "main_belt")

        self.assertEqual(loaded.asteroid_discovery_synthetic_sweep_direction_focus_half_width_deg, 22.5)

        self.assertEqual(loaded.asteroid_discovery_synthetic_sweep_min_stacked_snr, 7.5)

        self.assertTrue(loaded.asteroid_discovery_synthetic_sweep_save_stacks)

        self.assertEqual(loaded.asteroid_main_splitter_sizes, [730, 990])

        self.assertEqual(loaded.asteroid_results_splitter_sizes, [470, 230])

        self.assertTrue(loaded.synthetic_tracking_advanced_enabled)

        self.assertEqual(loaded.synthetic_tracking_backend_preference, "gpu")

        self.assertEqual(loaded.asteroid_track_object_position_mode, "measured")



    def test_settings_config_override_round_trip_uses_app_state(self) -> None:

        custom_path = Path(self._config_dir.name) / "custom-settings.json"



        save_settings_config_override(custom_path)



        self.assertEqual(load_settings_config_override(), custom_path)



    def test_from_root_uses_saved_settings_config_override_when_env_override_missing(self) -> None:

        os.environ.pop("CITIZEN_PHOTOMETRY_CONFIG_PATH", None)

        custom_path = Path(self._config_dir.name) / "custom-settings.json"

        custom_path.write_text('{"nearby_reference_count": 13}', encoding="utf-8")

        save_settings_config_override(custom_path)



        loaded = AppSettings.from_root(Path(self._config_dir.name))



        self.assertEqual(loaded.config_path, custom_path)

        self.assertEqual(loaded.nearby_reference_count, 13)



    def test_settings_round_trip_preserves_nearby_reference_count(self) -> None:

        with tempfile.TemporaryDirectory() as temp_dir:

            root = Path(temp_dir)

            settings = AppSettings(

                astrometry_api_key="demo-key",

                cache_dir=root / ".photometry-cache",

                config_path=root / ".photometry-settings.json",

                assume_aligned_images=True,

                nearby_reference_count=7,

                shared_parallel_workers=5,

                astrostack_parallel_workers=7,

                photometry_parallel_workers=5,

                calculate_period_parallel_workers=5,

                literature_period_parallel_workers=5,

                snr_binning_max_period_fraction=0.025,

                snr_binning_max_absolute_duration_seconds=480.0,

                snr_binning_target_snr=28.0,

                snr_binning_max_frames_per_bin=12,

                snr_binning_min_frames_per_bin=2,

                snr_binning_type_aware_thresholds=False,

                snr_binning_sharp_period_fraction=0.012,

                snr_binning_smooth_period_fraction=0.045,

                snr_binning_weighted_flux_binning=False,

                snr_binning_allow_magnitude_fallback=False,

                snr_binning_minimum_valid_points_per_bin=3,

                snr_binning_outlier_rejection_enabled=True,

                snr_binning_sigma_clip_threshold=4.2,

                snr_binning_dataset_mode="replace",

                snr_binning_apply_to_selected_measurements_only=True,

                snr_binning_allow_periodless_fallback=True,

                comparison_fit_stop_match_index=97.5,

                comparison_fit_parallel_workers=4,

                asteroid_search_parallel_workers=6,

                comparison_fit_allow_multiple_targets=True,

                comparison_fit_eclipsing_binary_match_tolerance=1.5,

                comparison_fit_fallback_candidate_pool_size=6,

                comparison_fit_fallback_magnitude_tolerance=0.65,

                discovery_max_candidate_count=42,

                discovery_min_magnitude=9.0,

                discovery_max_magnitude=14.0,

                discovery_min_candidate_score=31.5,

                light_curve_scientific_export_enabled=False,

                scientific_light_curve_pdf_dpi=450,

                scientific_light_curve_pdf_paper_size="A4",

                hr_table_row_limit=321,

                hr_plot_color_saturation=1.35,

                hr_plot_point_opacity=0.55,

                hr_plot_marker_size_mode="fixed",

                hr_plot_fixed_marker_size=10.5,

                hr_plot_require_parallax=False,

                hr_motion_vector_color="#ff8844",

                hr_motion_vector_color_by_angle=True,

                hr_motion_vector_saturation_by_magnitude=True,

                hr_motion_vector_width=2.75,

                hr_roi_drag_color="#aa5500",

                hr_roi_color="#336699",

                frame_edge_margin_percent=12.5,

                image_frame_margin_enabled=False,

                reference_star_min_magnitude=9.5,

                reference_star_max_magnitude=13.0,

                image_display_stretch_mode="log",

                image_display_curve_points=((0.0, 0.0), (0.35, 0.2), (1.0, 1.0)),

                image_display_brightness=-0.15,

                image_display_contrast=1.8,

                image_display_inverted=True,

                image_mark_saturated_enabled=False,

                asteroid_visual_show_known_objects=False,

                asteroid_visual_show_object_markers=False,

                asteroid_visual_show_potential_discoveries=False,

                asteroid_visual_label_all_objects=False,

                asteroid_visual_show_all_crosshairs=False,

                asteroid_visual_highlight_selected_object=False,

                asteroid_visual_invert_annotation_colors=False,

                asteroid_blink_frame_duration_ms=500,

                asteroid_gif_export_scale_percent=150,

                asteroid_gif_export_loop_forever=False,

                asteroid_manual_magnitude_limit_override_enabled=True,

                asteroid_manual_magnitude_limit_override=17.25,

                synthetic_tracking_crop_radius_pixels=512,

                synthetic_tracking_integration_mode="average",

                synthetic_tracking_weight_mode="psf_snr",

                synthetic_tracking_rejection_mode="sigma_clipping",

                synthetic_tracking_combine_mode="sigma_clipped_mean",

                synthetic_tracking_allow_mixed_all_group=True,

                asteroid_discovery_enable_synthetic_sweep=True,

                asteroid_discovery_synthetic_sweep_max_motion_px_per_hour=22.0,

                asteroid_discovery_synthetic_sweep_motion_step_px_per_hour=0.5,

                asteroid_discovery_synthetic_sweep_angle_step_deg=12.0,

                asteroid_discovery_synthetic_sweep_direction_focus="main_belt",

                asteroid_discovery_synthetic_sweep_direction_focus_half_width_deg=18.0,

                asteroid_discovery_synthetic_sweep_min_stacked_snr=8.5,

                asteroid_discovery_synthetic_sweep_save_stacks=True,

                observer_code="KAY",

                observer_name="Kay",

                organization="Citizen Photometry",

                site_name="Backyard",

                observing_site_latitude_deg=51.5074,

                observing_site_longitude_deg=-0.1278,

                observing_site_elevation_m=35.0,

                observing_site_presets=[
                    ObservingSitePreset(name="Backyard", latitude_deg=51.5074, longitude_deg=-0.1278, elevation_m=35.0),
                    ObservingSitePreset(name="Remote Dark Site", latitude_deg=-24.6270, longitude_deg=-70.4045, elevation_m=2635.0),
                ],

                telescope="80mm refractor",

                telescope_focal_length_mm=400.0,

                telescope_aperture_mm=80.0,

                telescope_focal_ratio=5.0,

                camera="Mono CMOS",

                camera_pixel_size_um=3.76,

                bortle_scale=5,

                filter_system="Johnson-Cousins",

                aavso_chart_id="X12345ABC",

                observation_timezone="America/New_York",

                time_standard="bjd_tdb",

                transformed=True,

                reduction_notes="Rejected cloudy frames.",

                preview_variable_star_max_count=250,

                preview_variable_star_min_magnitude=8.5,

                preview_variable_star_max_magnitude=15.5,

                theme="dark",

                photometry_aperture_mode=PhotometryApertureMode.FWHM_SCALED,

                aperture_radius_pixels=5.5,

                annulus_inner_radius_pixels=8.5,

                annulus_outer_radius_pixels=12.5,

                aperture_radius_fwhm_scale=1.7,

                annulus_inner_radius_fwhm_scale=3.2,

                annulus_outer_radius_fwhm_scale=4.8,

                variable_star_limit_mode=VariableStarLimitMode.COUNT,

                variable_star_limit_value=25,

                variable_star_designation_filters=[VariableStarDesignationFamily.NAMED, VariableStarDesignationFamily.GAIA],

            )



            settings.save(root)

            loaded = AppSettings.from_root(root)



            self.assertEqual(loaded.nearby_reference_count, 7)

            self.assertTrue(loaded.assume_aligned_images)

            self.assertEqual(loaded.shared_parallel_workers, 5)

            self.assertEqual(loaded.astrostack_parallel_workers, 7)

            self.assertEqual(loaded.photometry_parallel_workers, 5)

            self.assertEqual(loaded.calculate_period_parallel_workers, 5)

            self.assertEqual(loaded.literature_period_parallel_workers, 5)

            self.assertEqual(loaded.snr_binning_max_period_fraction, 0.025)

            self.assertEqual(loaded.snr_binning_max_absolute_duration_seconds, 480.0)

            self.assertEqual(loaded.snr_binning_target_snr, 28.0)

            self.assertEqual(loaded.snr_binning_max_frames_per_bin, 12)

            self.assertEqual(loaded.snr_binning_min_frames_per_bin, 2)

            self.assertFalse(loaded.snr_binning_type_aware_thresholds)

            self.assertEqual(loaded.snr_binning_sharp_period_fraction, 0.012)

            self.assertEqual(loaded.snr_binning_smooth_period_fraction, 0.045)

            self.assertFalse(loaded.snr_binning_weighted_flux_binning)

            self.assertFalse(loaded.snr_binning_allow_magnitude_fallback)

            self.assertEqual(loaded.snr_binning_minimum_valid_points_per_bin, 3)

            self.assertTrue(loaded.snr_binning_outlier_rejection_enabled)

            self.assertEqual(loaded.snr_binning_sigma_clip_threshold, 4.2)

            self.assertEqual(loaded.snr_binning_dataset_mode, "replace")

            self.assertTrue(loaded.snr_binning_apply_to_selected_measurements_only)

            self.assertTrue(loaded.snr_binning_allow_periodless_fallback)

            self.assertEqual(loaded.comparison_fit_stop_match_index, 97.5)

            self.assertEqual(loaded.comparison_fit_parallel_workers, 4)

            self.assertEqual(loaded.asteroid_search_parallel_workers, 6)

            self.assertTrue(loaded.comparison_fit_allow_multiple_targets)

            self.assertEqual(loaded.comparison_fit_eclipsing_binary_match_tolerance, 1.5)

            self.assertEqual(loaded.comparison_fit_fallback_candidate_pool_size, 6)

            self.assertEqual(loaded.comparison_fit_fallback_magnitude_tolerance, 0.65)

            self.assertEqual(loaded.discovery_max_candidate_count, 42)

            self.assertEqual(loaded.discovery_min_magnitude, 9.0)

            self.assertEqual(loaded.discovery_max_magnitude, 14.0)

            self.assertEqual(loaded.discovery_min_candidate_score, 31.5)

            self.assertFalse(loaded.light_curve_scientific_export_enabled)

            self.assertEqual(loaded.scientific_light_curve_pdf_dpi, 450)

            self.assertEqual(loaded.scientific_light_curve_pdf_paper_size, "A4")

            self.assertEqual(loaded.hr_plot_color_saturation, 1.35)

            self.assertEqual(loaded.hr_plot_point_opacity, 0.55)

            self.assertEqual(loaded.hr_plot_marker_size_mode, "fixed")

            self.assertEqual(loaded.hr_plot_fixed_marker_size, 10.5)

            self.assertFalse(loaded.hr_plot_require_parallax)

            self.assertEqual(loaded.hr_table_row_limit, 321)

            self.assertEqual(loaded.hr_motion_vector_color, "#ff8844")

            self.assertTrue(loaded.hr_motion_vector_color_by_angle)

            self.assertTrue(loaded.hr_motion_vector_saturation_by_magnitude)

            self.assertEqual(loaded.hr_motion_vector_width, 2.75)

            self.assertEqual(loaded.hr_roi_drag_color, "#aa5500")

            self.assertEqual(loaded.hr_roi_color, "#336699")

            self.assertEqual(loaded.frame_edge_margin_percent, 12.5)

            self.assertFalse(loaded.image_frame_margin_enabled)

            self.assertTrue(loaded.saturation_filter_enabled)

            self.assertEqual(loaded.image_display_stretch_mode, "log")

            self.assertEqual(loaded.image_display_curve_points, ((0.0, 0.0), (0.35, 0.2), (1.0, 1.0)))

            self.assertEqual(loaded.image_display_brightness, -0.15)

            self.assertEqual(loaded.image_display_contrast, 1.8)

            self.assertTrue(loaded.image_display_inverted)

            self.assertFalse(loaded.image_mark_saturated_enabled)

            self.assertFalse(loaded.asteroid_visual_show_known_objects)

            self.assertFalse(loaded.asteroid_visual_show_object_markers)

            self.assertFalse(loaded.asteroid_visual_show_potential_discoveries)

            self.assertFalse(loaded.asteroid_visual_label_all_objects)

            self.assertFalse(loaded.asteroid_visual_show_all_crosshairs)

            self.assertFalse(loaded.asteroid_visual_highlight_selected_object)

            self.assertFalse(loaded.asteroid_visual_invert_annotation_colors)

            self.assertEqual(loaded.asteroid_blink_frame_duration_ms, 500)

            self.assertEqual(loaded.asteroid_gif_export_scale_percent, 150)

            self.assertFalse(loaded.asteroid_gif_export_loop_forever)

            self.assertTrue(loaded.asteroid_manual_magnitude_limit_override_enabled)

            self.assertEqual(loaded.asteroid_manual_magnitude_limit_override, 17.25)

            self.assertEqual(loaded.synthetic_tracking_crop_radius_pixels, 512)

            self.assertEqual(loaded.synthetic_tracking_integration_mode, "average")

            self.assertEqual(loaded.synthetic_tracking_weight_mode, "psf_snr")

            self.assertEqual(loaded.synthetic_tracking_rejection_mode, "sigma_clipping")

            self.assertEqual(loaded.synthetic_tracking_combine_mode, "sigma_clipped_mean")

            self.assertTrue(loaded.synthetic_tracking_allow_mixed_all_group)

            self.assertTrue(loaded.asteroid_discovery_enable_synthetic_sweep)

            self.assertEqual(loaded.asteroid_discovery_synthetic_sweep_max_motion_px_per_hour, 22.0)

            self.assertEqual(loaded.asteroid_discovery_synthetic_sweep_motion_step_px_per_hour, 0.5)

            self.assertEqual(loaded.asteroid_discovery_synthetic_sweep_angle_step_deg, 12.0)

            self.assertEqual(loaded.asteroid_discovery_synthetic_sweep_direction_focus, "main_belt")

            self.assertEqual(loaded.asteroid_discovery_synthetic_sweep_direction_focus_half_width_deg, 18.0)

            self.assertEqual(loaded.asteroid_discovery_synthetic_sweep_min_stacked_snr, 8.5)

            self.assertTrue(loaded.asteroid_discovery_synthetic_sweep_save_stacks)

            self.assertEqual(loaded.reference_star_min_magnitude, 9.5)

            self.assertEqual(loaded.reference_star_max_magnitude, 13.0)

            self.assertEqual(loaded.observer_code, "KAY")

            self.assertEqual(loaded.observer_name, "Kay")

            self.assertEqual(loaded.organization, "Citizen Photometry")

            self.assertEqual(loaded.site_name, "Backyard")

            self.assertEqual(loaded.observing_site_latitude_deg, 51.5074)

            self.assertEqual(loaded.observing_site_longitude_deg, -0.1278)

            self.assertEqual(loaded.observing_site_elevation_m, 35.0)

            self.assertEqual(len(loaded.observing_site_presets or []), 2)

            assert loaded.observing_site_presets is not None

            self.assertEqual(loaded.observing_site_presets[0].name, "Backyard")

            self.assertEqual(loaded.observing_site_presets[1].name, "Remote Dark Site")

            self.assertEqual(loaded.telescope, "80mm refractor")

            self.assertEqual(loaded.telescope_focal_length_mm, 400.0)

            self.assertEqual(loaded.telescope_aperture_mm, 80.0)

            self.assertEqual(loaded.telescope_focal_ratio, 5.0)

            self.assertEqual(loaded.camera, "Mono CMOS")

            self.assertEqual(loaded.camera_pixel_size_um, 3.76)

            self.assertEqual(loaded.bortle_scale, 5)

            self.assertEqual(loaded.filter_system, "Johnson-Cousins")

            self.assertEqual(loaded.aavso_chart_id, "X12345ABC")

            self.assertEqual(loaded.observation_timezone, "America/New_York")

            self.assertEqual(loaded.time_standard, "BJD_TDB")

            self.assertTrue(loaded.transformed)

            self.assertEqual(loaded.reduction_notes, "Rejected cloudy frames.")

            self.assertEqual(loaded.preview_variable_star_max_count, 250)

            self.assertEqual(loaded.preview_variable_star_min_magnitude, 8.5)

            self.assertEqual(loaded.preview_variable_star_max_magnitude, 15.5)

    def test_settings_ignore_obsolete_sky_view_milky_way_interactive_low_quality_key(self) -> None:

        with tempfile.TemporaryDirectory() as temp_dir:

            root = Path(temp_dir)

            config_path = Path(os.environ["CITIZEN_PHOTOMETRY_CONFIG_PATH"])

            config_path.write_text('{"sky_view_milky_way_interactive_low_quality": false}', encoding="utf-8")

            loaded = AppSettings.from_root(root)

            self.assertIsInstance(loaded, AppSettings)



    def test_legacy_parallel_worker_settings_migrate_to_shared_value(self) -> None:

        with tempfile.TemporaryDirectory() as temp_dir:

            root = Path(temp_dir)

            config_path = root / ".photometry-settings.json"

            config_path.write_text(
                json.dumps(
                    {
                        "cache_dir": str(root / ".photometry-cache"),
                        "photometry_parallel_workers": 2,
                        "calculate_period_parallel_workers": 5,
                        "literature_period_parallel_workers": 3,
                    }
                ),
                encoding="utf-8",
            )

            loaded = AppSettings.from_root(root)

            self.assertEqual(loaded.shared_parallel_workers, 5)

            self.assertEqual(loaded.photometry_parallel_workers, 5)

            self.assertEqual(loaded.calculate_period_parallel_workers, 5)

            self.assertEqual(loaded.literature_period_parallel_workers, 5)

    def test_legacy_known_object_visibility_setting_keeps_labels_hidden(self) -> None:

        with tempfile.TemporaryDirectory() as temp_dir:

            root = Path(temp_dir)

            config_path = Path(os.environ["CITIZEN_PHOTOMETRY_CONFIG_PATH"])

            config_path.write_text(

                json.dumps(

                    {

                        "asteroid_visual_show_known_objects": False,

                        "asteroid_visual_label_all_objects": True,

                    }

                ),

                encoding="utf-8",

            )



            loaded = AppSettings.from_root(root)



            self.assertFalse(loaded.asteroid_visual_show_known_objects)

            self.assertFalse(loaded.asteroid_visual_show_object_markers)

            self.assertFalse(loaded.asteroid_visual_label_all_objects)



    def test_settings_clamps_observing_site_coordinates_to_valid_ranges(self) -> None:

        with tempfile.TemporaryDirectory() as temp_dir:

            root = Path(temp_dir)

            config_path = Path(os.environ["CITIZEN_PHOTOMETRY_CONFIG_PATH"])

            config_path.write_text(

                json.dumps(

                    {

                        "observing_site_latitude_deg": 120.0,

                        "observing_site_longitude_deg": -250.0,

                        "observing_site_elevation_m": 15000.0,

                    }

                ),

                encoding="utf-8",

            )



            loaded = AppSettings.from_root(root)



            self.assertEqual(loaded.observing_site_latitude_deg, 90.0)

            self.assertEqual(loaded.observing_site_longitude_deg, -180.0)

            self.assertEqual(loaded.observing_site_elevation_m, 12000.0)



    def test_settings_legacy_preview_max_magnitude_is_preserved_without_minimum(self) -> None:

        with tempfile.TemporaryDirectory() as temp_dir:

            root = Path(temp_dir)

            config_path = Path(os.environ["CITIZEN_PHOTOMETRY_CONFIG_PATH"])

            config_path.write_text('{"preview_variable_star_max_magnitude": 12.5}', encoding="utf-8")



            loaded = AppSettings.from_root(root)



            self.assertIsNone(loaded.preview_variable_star_min_magnitude)

            self.assertEqual(loaded.preview_variable_star_max_magnitude, 12.5)



    def test_settings_reference_star_magnitude_range_is_normalized(self) -> None:

        with tempfile.TemporaryDirectory() as temp_dir:

            root = Path(temp_dir)

            config_path = Path(os.environ["CITIZEN_PHOTOMETRY_CONFIG_PATH"])

            config_path.write_text('{"reference_star_min_magnitude": 13.5, "reference_star_max_magnitude": 9.0}', encoding="utf-8")



            loaded = AppSettings.from_root(root)



            self.assertEqual(loaded.reference_star_min_magnitude, 9.0)

            self.assertEqual(loaded.reference_star_max_magnitude, 13.5)



    def test_settings_metadata_text_is_trimmed_and_time_standard_defaults_to_utc(self) -> None:

        with tempfile.TemporaryDirectory() as temp_dir:

            root = Path(temp_dir)

            config_path = Path(os.environ["CITIZEN_PHOTOMETRY_CONFIG_PATH"])

            config_path.write_text(

                json.dumps(

                    {

                        "observer_code": "  KAY  ",

                        "aavso_chart_id": "  123-ABC  ",

                        "reduction_notes": "  First pass only.\nChecked manually.  ",

                        "time_standard": "   ",

                    }

                ),

                encoding="utf-8",

            )



            loaded = AppSettings.from_root(root)



            self.assertEqual(loaded.observer_code, "KAY")

            self.assertEqual(loaded.aavso_chart_id, "123-ABC")

            self.assertEqual(loaded.reduction_notes, "First pass only.\nChecked manually.")

            self.assertEqual(loaded.time_standard, "UTC")



    def test_settings_round_trip_preserves_app_mode(self) -> None:

        with tempfile.TemporaryDirectory() as temp_dir:

            root = Path(temp_dir)

            settings = AppSettings(

                astrometry_api_key=None,

                cache_dir=root / ".photometry-cache",

                config_path=root / ".photometry-settings.json",

                assume_aligned_images=False,

                nearby_reference_count=5,

                photometry_aperture_mode=PhotometryApertureMode.FWHM_SCALED,

                aperture_radius_pixels=5.0,

                annulus_inner_radius_pixels=8.0,

                annulus_outer_radius_pixels=12.0,

                aperture_radius_fwhm_scale=1.6,

                annulus_inner_radius_fwhm_scale=3.0,

                annulus_outer_radius_fwhm_scale=4.5,

                variable_star_limit_mode=VariableStarLimitMode.PERCENT,

                variable_star_limit_value=25,

                variable_star_designation_filters=[VariableStarDesignationFamily.NAMED],

                app_mode=AppMode.HR_DIAGRAM,

            )



            settings.save(root)

            loaded = AppSettings.from_root(root)



            self.assertEqual(loaded.app_mode, AppMode.HR_DIAGRAM)



    def test_settings_round_trip_preserves_saturation_filter_toggle(self) -> None:

        with tempfile.TemporaryDirectory() as temp_dir:

            root = Path(temp_dir)

            settings = AppSettings(

                astrometry_api_key=None,

                cache_dir=root / ".photometry-cache",

                config_path=root / ".photometry-settings.json",

                assume_aligned_images=False,

                nearby_reference_count=5,

                photometry_aperture_mode=PhotometryApertureMode.FWHM_SCALED,

                aperture_radius_pixels=5.0,

                annulus_inner_radius_pixels=8.0,

                annulus_outer_radius_pixels=12.0,

                aperture_radius_fwhm_scale=1.6,

                annulus_inner_radius_fwhm_scale=3.0,

                annulus_outer_radius_fwhm_scale=4.5,

                variable_star_limit_mode=VariableStarLimitMode.PERCENT,

                variable_star_limit_value=25,

                variable_star_designation_filters=[VariableStarDesignationFamily.NAMED],

                saturation_filter_enabled=False,

            )



            settings.save(root)

            loaded = AppSettings.from_root(root)



            self.assertFalse(loaded.saturation_filter_enabled)



    def test_settings_round_trip_preserves_custom_theme_colors(self) -> None:

        with tempfile.TemporaryDirectory() as temp_dir:

            root = Path(temp_dir)

            settings = AppSettings(

                astrometry_api_key=None,

                cache_dir=root / ".photometry-cache",

                config_path=root / ".photometry-settings.json",

                assume_aligned_images=False,

                nearby_reference_count=5,

                photometry_aperture_mode=PhotometryApertureMode.FWHM_SCALED,

                aperture_radius_pixels=5.0,

                annulus_inner_radius_pixels=8.0,

                annulus_outer_radius_pixels=12.0,

                aperture_radius_fwhm_scale=1.6,

                annulus_inner_radius_fwhm_scale=3.0,

                annulus_outer_radius_fwhm_scale=4.5,

                variable_star_limit_mode=VariableStarLimitMode.PERCENT,

                variable_star_limit_value=25,

                variable_star_designation_filters=[VariableStarDesignationFamily.NAMED],

                theme="custom",

                custom_theme_colors={

                    "window_bg": "#101820",

                    "panel_bg": "#17232e",

                    "text": "#f2f4f8",

                    "menu_bg": "#203040",

                    "menu_text": "#f2f4f8",

                    "accent": "#ffb703",

                    "plot_bg": "#0b141b",

                    "plot_axis": "#d9e2ec",

                    "plot_points": "#8ecae6",

                    "plot_fit": "#fb8500",

                    "ra_grid": "#219ebc",

                    "dec_grid": "#ffb703",

                    "asteroid_other_overlay_circle_color": "#ffd166",

                    "asteroid_other_overlay_line_color": "#073b4c",

                    "asteroid_other_overlay_text_color": "#f1faee",

                    "asteroid_other_overlay_line_width": "2.75",

                    "asteroid_other_overlay_text_size": "11.5",

                    "asteroid_overlay_circle_color": "#8ecae6",

                    "asteroid_overlay_line_color": "#023047",

                    "asteroid_overlay_text_color": "#ffffff",

                    "asteroid_overlay_line_width": "2.25",

                    "asteroid_overlay_text_size": "13.5",

                },

                equatorial_grid_ra_density=7,

                equatorial_grid_dec_density=9,

                asteroid_visual_show_target_marker=True,

                asteroid_target_marker_line_color="#991b1b",

                asteroid_target_marker_accent_color="#fb7185",

                asteroid_target_marker_text_color="#ffe4e6",

                asteroid_target_marker_outline_color="#1f2937",

                asteroid_target_marker_line_width=4.5,

                asteroid_mp4_export_scale_percent=175,

                image_equatorial_grid_enabled=True,

                image_frame_margin_enabled=False,

                image_mark_saturated_enabled=False,

            )



            settings.save(root)

            loaded = AppSettings.from_root(root)



            self.assertEqual(loaded.theme, "custom")

            self.assertEqual((loaded.custom_theme_colors or {}).get("accent"), "#ffb703")

            self.assertEqual((loaded.custom_theme_colors or {}).get("plot_fit"), "#fb8500")

            self.assertEqual((loaded.custom_theme_colors or {}).get("ra_grid"), "#219ebc")

            self.assertEqual((loaded.custom_theme_colors or {}).get("dec_grid"), "#ffb703")

            self.assertEqual((loaded.custom_theme_colors or {}).get("asteroid_other_overlay_circle_color"), "#ffd166")

            self.assertEqual((loaded.custom_theme_colors or {}).get("asteroid_other_overlay_line_width"), "2.75")

            self.assertEqual((loaded.custom_theme_colors or {}).get("asteroid_other_overlay_text_size"), "11.5")

            self.assertEqual((loaded.custom_theme_colors or {}).get("asteroid_overlay_circle_color"), "#8ecae6")

            self.assertEqual((loaded.custom_theme_colors or {}).get("asteroid_overlay_line_width"), "2.25")

            self.assertEqual((loaded.custom_theme_colors or {}).get("asteroid_overlay_text_size"), "13.5")

            self.assertTrue(loaded.asteroid_visual_show_target_marker)

            self.assertEqual(loaded.asteroid_target_marker_line_color, "#991b1b")

            self.assertEqual(loaded.asteroid_target_marker_accent_color, "#fb7185")

            self.assertEqual(loaded.asteroid_target_marker_text_color, "#ffe4e6")

            self.assertEqual(loaded.asteroid_target_marker_outline_color, "#1f2937")

            self.assertEqual(loaded.asteroid_target_marker_line_width, 4.5)

            self.assertEqual(loaded.asteroid_mp4_export_scale_percent, 175)

            self.assertEqual(loaded.equatorial_grid_ra_density, 7)

            self.assertEqual(loaded.equatorial_grid_dec_density, 9)

            self.assertTrue(loaded.image_equatorial_grid_enabled)

            self.assertFalse(loaded.image_frame_margin_enabled)

            self.assertFalse(loaded.image_mark_saturated_enabled)


    def test_settings_round_trip_preserves_sky_explorer_manual_search_and_style_overrides(self) -> None:

        with tempfile.TemporaryDirectory() as temp_dir:

            root = Path(temp_dir)

            settings = AppSettings.from_root(root)

            settings.sky_explorer_simbad_search_radius_arcsec = 24.5

            settings.sky_explorer_gaia_max_magnitude = 16.7

            settings.sky_explorer_gaia_hard_cap_enabled = True

            settings.sky_explorer_gaia_hard_cap_rows = 432

            settings.sky_explorer_mag_limit_examples_per_bin = 4

            settings.sky_explorer_mag_limit_marker_color = "#ff00aa"

            settings.sky_explorer_mag_limit_marker_stroke_color = "#223344"

            settings.sky_explorer_mag_limit_marker_stroke_width = 2.5

            settings.sky_explorer_mag_limit_target_size = 8.0

            settings.sky_explorer_mag_limit_text_color = "#00ffaa"

            settings.sky_explorer_mag_limit_text_stroke_color = "#ddeeff"

            settings.sky_explorer_mag_limit_text_stroke_width = 1.0

            settings.sky_explorer_mag_limit_text_size = 11.0

            settings.sky_explorer_enabled_layers = ("deep_sky", "solar_system")

            settings.sky_explorer_fill_opacity = 0.4

            settings.sky_explorer_stroke_opacity = 0.85

            settings.sky_explorer_object_group_color_overrides = {

                "galaxy": "#aa3300",

                "cluster": "#ddcc44",

            }

            settings.sky_explorer_object_type_color_overrides = {

                "galaxy": ("#112233", "#aabbcc"),

                "planetary_nebula": ("#334455", "#ddeeff"),

            }

            settings.sky_explorer_object_type_text_color_overrides = {

                "galaxy": "#556677",

            }

            settings.sky_explorer_object_type_font_overrides = {

                "galaxy": "Segoe UI,12,-1,5,700,1,0,0,0,0",

            }

            settings.save(root)

            loaded = AppSettings.from_root(root)


        self.assertEqual(loaded.sky_explorer_simbad_search_radius_arcsec, 24.5)

        self.assertEqual(loaded.sky_explorer_gaia_max_magnitude, 16.7)

        self.assertTrue(loaded.sky_explorer_gaia_hard_cap_enabled)

        self.assertEqual(loaded.sky_explorer_gaia_hard_cap_rows, 432)

        self.assertEqual(loaded.sky_explorer_mag_limit_examples_per_bin, 4)

        self.assertEqual(loaded.sky_explorer_mag_limit_marker_color, "#ff00aa")

        self.assertEqual(loaded.sky_explorer_mag_limit_marker_stroke_color, "#223344")

        self.assertEqual(loaded.sky_explorer_mag_limit_marker_stroke_width, 2.5)

        self.assertEqual(loaded.sky_explorer_mag_limit_target_size, 8.0)

        self.assertEqual(loaded.sky_explorer_mag_limit_text_color, "#00ffaa")

        self.assertEqual(loaded.sky_explorer_mag_limit_text_stroke_color, "#ddeeff")

        self.assertEqual(loaded.sky_explorer_mag_limit_text_stroke_width, 1.0)

        self.assertEqual(loaded.sky_explorer_mag_limit_text_size, 11.0)

        self.assertEqual(loaded.sky_explorer_enabled_layers, ("deep_sky", "solar_system"))

        self.assertEqual(loaded.sky_explorer_fill_opacity, 0.4)

        self.assertEqual(loaded.sky_explorer_stroke_opacity, 0.85)

        self.assertEqual((loaded.sky_explorer_object_group_color_overrides or {}).get("galaxy"), "#aa3300")

        self.assertEqual((loaded.sky_explorer_object_group_color_overrides or {}).get("cluster"), "#ddcc44")

        self.assertEqual((loaded.sky_explorer_object_type_color_overrides or {}).get("galaxy"), ("#112233", "#aabbcc"))

        self.assertEqual((loaded.sky_explorer_object_type_color_overrides or {}).get("planetary_nebula"), ("#334455", "#ddeeff"))

        self.assertEqual((loaded.sky_explorer_object_type_text_color_overrides or {}).get("galaxy"), "#556677")

        self.assertEqual((loaded.sky_explorer_object_type_font_overrides or {}).get("galaxy"), "Segoe UI,12,-1,5,700,1,0,0,0,0")


    def test_settings_migrates_legacy_sky_explorer_object_type_column_widths(self) -> None:

        with tempfile.TemporaryDirectory() as temp_dir:

            root = Path(temp_dir)

            config_path = Path(os.environ["CITIZEN_PHOTOMETRY_CONFIG_PATH"])

            config_path.write_text(

                json.dumps({"sky_explorer_object_type_column_widths": [127, 133, 277, 215, 403]}),

                encoding="utf-8",

            )

            loaded = AppSettings.from_root(root)

        self.assertEqual(loaded.sky_explorer_object_type_column_widths, [127, 133, 277, 150, 215, 403])


    def test_settings_migrates_legacy_sky_explorer_results_column_widths(self) -> None:

        with tempfile.TemporaryDirectory() as temp_dir:

            root = Path(temp_dir)

            config_path = Path(os.environ["CITIZEN_PHOTOMETRY_CONFIG_PATH"])

            config_path.write_text(

                json.dumps({"sky_explorer_results_column_widths": [142, 203, 164, 83, 125, 127, 93]}),

                encoding="utf-8",

            )

            loaded = AppSettings.from_root(root)

        self.assertEqual(loaded.sky_explorer_results_column_widths, [203, 164, 83, 125, 127, 93])



    def test_settings_legacy_equatorial_grid_density_populates_both_axes(self) -> None:

        with tempfile.TemporaryDirectory() as temp_dir:

            root = Path(temp_dir)

            config_path = Path(os.environ["CITIZEN_PHOTOMETRY_CONFIG_PATH"])

            config_path.write_text('{"equatorial_grid_density": 8}', encoding="utf-8")



            loaded = AppSettings.from_root(root)



            self.assertEqual(loaded.equatorial_grid_ra_density, 8)

            self.assertEqual(loaded.equatorial_grid_dec_density, 8)



    def test_settings_round_trip_preserves_builtin_dark_preset(self) -> None:

        with tempfile.TemporaryDirectory() as temp_dir:

            root = Path(temp_dir)

            settings = AppSettings(

                astrometry_api_key=None,

                cache_dir=root / ".photometry-cache",

                config_path=root / ".photometry-settings.json",

                assume_aligned_images=False,

                nearby_reference_count=5,

                photometry_aperture_mode=PhotometryApertureMode.FWHM_SCALED,

                aperture_radius_pixels=5.0,

                annulus_inner_radius_pixels=8.0,

                annulus_outer_radius_pixels=12.0,

                aperture_radius_fwhm_scale=1.6,

                annulus_inner_radius_fwhm_scale=3.0,

                annulus_outer_radius_fwhm_scale=4.5,

                variable_star_limit_mode=VariableStarLimitMode.PERCENT,

                variable_star_limit_value=25,

                variable_star_designation_filters=[VariableStarDesignationFamily.NAMED],

                theme="dracula",

            )



            settings.save(root)

            loaded = AppSettings.from_root(root)



            self.assertEqual(loaded.theme, "dracula")



    def test_settings_round_trip_preserves_additional_builtin_theme_preset(self) -> None:

        with tempfile.TemporaryDirectory() as temp_dir:

            root = Path(temp_dir)

            settings = AppSettings(

                astrometry_api_key=None,

                cache_dir=root / ".photometry-cache",

                config_path=root / ".photometry-settings.json",

                assume_aligned_images=False,

                nearby_reference_count=5,

                photometry_aperture_mode=PhotometryApertureMode.FWHM_SCALED,

                aperture_radius_pixels=5.0,

                annulus_inner_radius_pixels=8.0,

                annulus_outer_radius_pixels=12.0,

                aperture_radius_fwhm_scale=1.6,

                annulus_inner_radius_fwhm_scale=3.0,

                annulus_outer_radius_fwhm_scale=4.5,

                variable_star_limit_mode=VariableStarLimitMode.PERCENT,

                variable_star_limit_value=25,

                variable_star_designation_filters=[VariableStarDesignationFamily.NAMED],

                theme="tokyo-night",

            )



            settings.save(root)

            loaded = AppSettings.from_root(root)



            self.assertEqual(loaded.theme, "tokyo-night")



    def test_settings_round_trip_preserves_period_caches(self) -> None:

        with tempfile.TemporaryDirectory() as temp_dir:

            root = Path(temp_dir)

            settings = AppSettings(

                astrometry_api_key=None,

                cache_dir=root / ".photometry-cache",

                config_path=root / ".photometry-settings.json",

                assume_aligned_images=False,

                nearby_reference_count=5,

                photometry_aperture_mode=PhotometryApertureMode.FWHM_SCALED,

                aperture_radius_pixels=5.0,

                annulus_inner_radius_pixels=8.0,

                annulus_outer_radius_pixels=12.0,

                aperture_radius_fwhm_scale=1.6,

                annulus_inner_radius_fwhm_scale=3.0,

                annulus_outer_radius_fwhm_scale=4.5,

                variable_star_limit_mode=VariableStarLimitMode.PERCENT,

                variable_star_limit_value=25,

                variable_star_designation_filters=[VariableStarDesignationFamily.NAMED],

                literature_period_cache={

                    "Demo": {

                        "vsx|vsx-1": {

                            "period_days": 1.2345,

                            "eclipse_duration_hours": 2.5,

                            "source": "VSX",

                        }

                    }

                },

                calculated_period_cache={

                    "Demo": {

                        "vsx-1|R": {

                            "period_hours": 12.0,

                            "periodic_harmonics": 2,

                            "method": "harmonic_fit",

                            "eclipse_duration_hours": 1.5,

                            "origin": "comparison_fit",

                            "comparison_source_ids": ["gaia-1", "gaia-2"],

                        }

                    }

                },

            )



            settings.save(root)

            loaded = AppSettings.from_root(root)



            self.assertEqual(loaded.literature_period_cache, settings.literature_period_cache)

            self.assertEqual(loaded.calculated_period_cache, settings.calculated_period_cache)



    def test_settings_round_trip_preserves_new_named_theme_preset(self) -> None:

        with tempfile.TemporaryDirectory() as temp_dir:

            root = Path(temp_dir)

            settings = AppSettings(

                astrometry_api_key=None,

                cache_dir=root / ".photometry-cache",

                config_path=root / ".photometry-settings.json",

                assume_aligned_images=False,

                nearby_reference_count=5,

                photometry_aperture_mode=PhotometryApertureMode.FWHM_SCALED,

                aperture_radius_pixels=5.0,

                annulus_inner_radius_pixels=8.0,

                annulus_outer_radius_pixels=12.0,

                aperture_radius_fwhm_scale=1.6,

                annulus_inner_radius_fwhm_scale=3.0,

                annulus_outer_radius_fwhm_scale=4.5,

                variable_star_limit_mode=VariableStarLimitMode.PERCENT,

                variable_star_limit_value=25,

                variable_star_designation_filters=[VariableStarDesignationFamily.NAMED],

                theme="catppuccin",

            )



            settings.save(root)

            loaded = AppSettings.from_root(root)



            self.assertEqual(loaded.theme, "catppuccin")



    def test_settings_are_shared_between_files_and_object_folder_selections(self) -> None:

        with tempfile.TemporaryDirectory() as temp_dir:

            workspace_root = Path(temp_dir)

            files_root = workspace_root / "Files"

            object_root = files_root / "M42"

            object_root.mkdir(parents=True)



            settings = AppSettings(

                astrometry_api_key="demo-key",

                cache_dir=workspace_root / ".photometry-cache",

                config_path=workspace_root / ".photometry-settings.json",

                assume_aligned_images=False,

                nearby_reference_count=9,

                photometry_aperture_mode=PhotometryApertureMode.FIXED,

                aperture_radius_pixels=6.0,

                annulus_inner_radius_pixels=9.0,

                annulus_outer_radius_pixels=13.5,

                aperture_radius_fwhm_scale=1.6,

                annulus_inner_radius_fwhm_scale=3.0,

                annulus_outer_radius_fwhm_scale=4.5,

                variable_star_limit_mode=VariableStarLimitMode.PERCENT,

                variable_star_limit_value=15,

                variable_star_designation_filters=[VariableStarDesignationFamily.NAMED],

            )



            settings.save(object_root)



            loaded_from_files = AppSettings.from_root(files_root)

            loaded_from_object = AppSettings.from_root(object_root)



            self.assertTrue(Path(os.environ["CITIZEN_PHOTOMETRY_CONFIG_PATH"]).exists())

            self.assertEqual(loaded_from_files.nearby_reference_count, 9)

            self.assertEqual(loaded_from_files.photometry_aperture_mode, PhotometryApertureMode.FWHM_SCALED)

            self.assertEqual(loaded_from_files.variable_star_limit_value, 15)

            self.assertEqual(loaded_from_files.variable_star_designation_filters, [VariableStarDesignationFamily.NAMED])

            self.assertEqual(loaded_from_object.nearby_reference_count, 9)

            self.assertEqual(loaded_from_object.photometry_aperture_mode, PhotometryApertureMode.FWHM_SCALED)

            self.assertEqual(loaded_from_object.variable_star_limit_value, 15)

            self.assertEqual(loaded_from_object.variable_star_designation_filters, [VariableStarDesignationFamily.NAMED])



    def test_settings_from_root_still_reads_legacy_workspace_file(self) -> None:

        with tempfile.TemporaryDirectory() as temp_dir:

            root = Path(temp_dir)

            legacy_settings_path = root / ".photometry-settings.json"

            legacy_settings_path.write_text(

                "{\n  \"nearby_reference_count\": 11,\n  \"frame_edge_margin_percent\": 7.5\n}",

                encoding="utf-8",

            )



            loaded = AppSettings.from_root(root)



            self.assertEqual(loaded.nearby_reference_count, 11)

            self.assertEqual(loaded.frame_edge_margin_percent, 7.5)



    def test_settings_round_trip_preserves_manual_configs_and_presets(self) -> None:

        with tempfile.TemporaryDirectory() as temp_dir:

            root = Path(temp_dir)

            settings = AppSettings(

                astrometry_api_key=None,

                cache_dir=root / ".photometry-cache",

                config_path=root / ".photometry-settings.json",

                assume_aligned_images=False,

                nearby_reference_count=5,

                photometry_aperture_mode=PhotometryApertureMode.FIXED,

                aperture_radius_pixels=6.0,

                annulus_inner_radius_pixels=9.0,

                annulus_outer_radius_pixels=13.0,

                aperture_radius_fwhm_scale=1.6,

                annulus_inner_radius_fwhm_scale=3.0,

                annulus_outer_radius_fwhm_scale=4.5,

                variable_star_limit_mode=VariableStarLimitMode.PERCENT,

                variable_star_limit_value=25,

                variable_star_designation_filters=[VariableStarDesignationFamily.NAMED],

                manual_photometry_configs={

                    "M42": ManualPhotometryConfig(

                        object_name="M42",

                        mode=ObjectPhotometryMode.MANUAL,

                        reference_frame_name="frame_001.fits",

                        keep_comparison_stars=False,

                        recenter_mode=RecenterMode.CENTROID_LIMITED,

                        max_recenter_radius_pixels=4.5,

                        fallback_to_wcs_on_centroid_failure=True,

                        sources=[

                            ManualSourceConfig(

                                source_id="manual-target-1",

                                name="M42",

                                role=ManualSourceRole.TARGET,

                                ra_deg=83.822,

                                dec_deg=-5.391,

                                reference_frame_name="frame_001.fits",

                                reference_x=120.0,

                                reference_y=98.0,

                                aperture_radius=6.0,

                                annulus_inner_radius=9.0,

                                annulus_outer_radius=13.0,

                            ),

                            ManualSourceConfig(

                                source_id="manual-comp-1",

                                name="Comp 1",

                                role=ManualSourceRole.COMPARISON,

                                ra_deg=83.824,

                                dec_deg=-5.389,

                                reference_frame_name="frame_001.fits",

                                reference_x=144.0,

                                reference_y=112.0,

                                aperture_radius=6.0,

                                annulus_inner_radius=9.0,

                                annulus_outer_radius=13.0,

                            ),

                        ],

                    )

                },

                aperture_presets=[

                    AperturePreset(

                        name="Repeatable set",

                        aperture_radius=6.0,

                        annulus_inner_radius=9.0,

                        annulus_outer_radius=13.0,

                        recenter_mode=RecenterMode.CENTROID_LIMITED,

                        max_recenter_radius_pixels=4.5,

                        fallback_to_wcs_on_centroid_failure=True,

                        comparison_source_ids=["manual-comp-1"],

                    )

                ],

            )



            settings.save(root)

            loaded = AppSettings.from_root(root)



            self.assertIn("M42", loaded.manual_photometry_configs or {})

            manual_config = (loaded.manual_photometry_configs or {})["M42"]

            self.assertEqual(manual_config.mode, ObjectPhotometryMode.MANUAL)

            self.assertFalse(manual_config.keep_comparison_stars)

            self.assertEqual(manual_config.recenter_mode, RecenterMode.CENTROID_LIMITED)

            self.assertEqual(len(manual_config.sources), 2)

            self.assertEqual(manual_config.target_source.name, "M42")

            self.assertEqual(len(loaded.aperture_presets or []), 1)

            self.assertEqual((loaded.aperture_presets or [])[0].name, "Repeatable set")



    def test_settings_round_trip_preserves_selected_catalog_source_ids(self) -> None:

        with tempfile.TemporaryDirectory() as temp_dir:

            root = Path(temp_dir)

            settings = AppSettings(

                astrometry_api_key=None,

                cache_dir=root / ".photometry-cache",

                config_path=root / ".photometry-settings.json",

                assume_aligned_images=False,

                nearby_reference_count=5,

                photometry_aperture_mode=PhotometryApertureMode.FIXED,

                aperture_radius_pixels=6.0,

                annulus_inner_radius_pixels=9.0,

                annulus_outer_radius_pixels=13.0,

                aperture_radius_fwhm_scale=1.6,

                annulus_inner_radius_fwhm_scale=3.0,

                annulus_outer_radius_fwhm_scale=4.5,

                variable_star_limit_mode=VariableStarLimitMode.PERCENT,

                variable_star_limit_value=25,

                variable_star_designation_filters=[VariableStarDesignationFamily.NAMED],

                selected_catalog_source_ids={

                    "M42": ["vsx:var-mid", "nasa:exo-1"],

                },

            )



            settings.save(root)

            loaded = AppSettings.from_root(root)



            self.assertEqual((loaded.selected_catalog_source_ids or {}).get("M42"), ["vsx:var-mid", "nasa:exo-1"])
