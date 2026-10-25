from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from PySide6.QtCore import QRectF, QSize, Qt
from PySide6.QtGui import QColor, QImage, QPainter, QPixmap
from PySide6.QtWidgets import QApplication, QLabel, QWidget

from photometry_app.app_metadata import APP_DISPLAY_NAME, APP_VERSION
from photometry_app.core.models import AppMode
from photometry_app.core.settings import AppSettings
from photometry_app.ui.mode_launcher import (
    EXPLORE_LEARN_ENTRIES,
    LAUNCHER_HEADER_SUBTITLE,
    MODE_LAUNCHER_ENTRIES,
    SCIENCE_WORKFLOW_ENTRIES,
    SCIENCE_WORKFLOW_LABEL,
    SIMULATION_ENTRIES,
    SIMULATIONS_LABEL,
    VISUALIZATIONS_TOOLS_LABEL,
    _CONTENT_MAX_WIDTH,
    ModeLauncherWidget,
    launcher_content_width,
    _draw_cover_pixmap,
    _resolve_mode_launcher_image,
    launcher_grid_column_count,
)


class ModeLauncherSettingsTest(unittest.TestCase):
    def setUp(self) -> None:
        self._config_dir = tempfile.TemporaryDirectory()
        self._previous_config_path = os.environ.get("CITIZEN_PHOTOMETRY_CONFIG_PATH")
        os.environ["CITIZEN_PHOTOMETRY_CONFIG_PATH"] = str(Path(self._config_dir.name) / "settings.json")

    def tearDown(self) -> None:
        if self._previous_config_path is None:
            os.environ.pop("CITIZEN_PHOTOMETRY_CONFIG_PATH", None)
        else:
            os.environ["CITIZEN_PHOTOMETRY_CONFIG_PATH"] = self._previous_config_path
        self._config_dir.cleanup()

    def test_show_mode_launcher_defaults_to_true(self) -> None:
        settings = AppSettings.defaults(Path(self._config_dir.name))
        self.assertTrue(settings.show_mode_launcher_on_startup)


class ModeLauncherWidgetTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls._app = QApplication.instance() or QApplication([])

    def test_launcher_lists_primary_modes(self) -> None:
        modes = {entry.mode for entry in MODE_LAUNCHER_ENTRIES}
        self.assertEqual(
            modes,
            {
                AppMode.DIFFERENTIAL_PHOTOMETRY,
                AppMode.ASTEROID_COMET_DETECTION,
                AppMode.HR_DIAGRAM,
                AppMode.TRANSIENT_FINDER,
                AppMode.SKY_EXPLORER,
                AppMode.SKY_VIEW,
                AppMode.DISTANCE_MAP,
                AppMode.ASTROSTACK,
                AppMode.BLACK_HOLE,
                AppMode.GALAXY_MERGER,
                AppMode.CLUSTER_FLOW,
                AppMode.BINARY_LIGHT_LAB,
                AppMode.AURORA_MAGNETOSPHERE,
            },
        )

    def test_science_workflow_row_order(self) -> None:
        science_titles = [entry.title for entry in SCIENCE_WORKFLOW_ENTRIES]
        self.assertEqual(
            science_titles,
            ["Differential Photometry", "Asteroid / Comet Detection", "Transient Finder"],
        )

    def test_visualizations_tools_row_order(self) -> None:
        explore_titles = [entry.title for entry in EXPLORE_LEARN_ENTRIES]
        self.assertEqual(
            explore_titles,
            ["Sky Atlas", "Sky Explorer", "HR Diagram", "Distance Map", "Deep Stack"],
        )

    def test_section_labels_name_the_workflows(self) -> None:
        self.assertEqual(SCIENCE_WORKFLOW_LABEL, "Detect & Measure")
        self.assertEqual(VISUALIZATIONS_TOOLS_LABEL, "Explore, Visualize & Process")
        self.assertEqual(SIMULATIONS_LABEL, "Simulations")
        self.assertEqual([entry.title for entry in SIMULATION_ENTRIES], ["Black Hole", "Galaxy Merger", "Cluster Flow", "Binary Light Lab", "Aurora & Magnetosphere Lab"])

    def test_launcher_header_names_the_app_and_version(self) -> None:
        launcher = ModeLauncherWidget()
        title = launcher.findChild(QLabel, "modeLauncherHeaderTitle")
        subtitle = launcher.findChild(QLabel, "modeLauncherHeaderSubtitle")
        version = launcher.findChild(QLabel, "modeLauncherVersion")
        self.assertIsNotNone(title)
        self.assertIsNotNone(subtitle)
        self.assertIsNotNone(version)
        assert title is not None and subtitle is not None and version is not None
        self.assertEqual(title.text(), APP_DISPLAY_NAME)
        self.assertEqual(subtitle.text(), LAUNCHER_HEADER_SUBTITLE)
        self.assertEqual(version.text(), APP_VERSION)
        self.assertIs(launcher.findChild(QLabel, "modeLauncherIdentity"), None)

    def test_launcher_places_section_headings_with_their_cards(self) -> None:
        launcher = ModeLauncherWidget()
        launcher.resize(1675, 895)
        launcher.show()
        QApplication.processEvents()
        try:
            science_title = launcher.findChild(QLabel, "modeLauncherSectionScience")
            explore_title = launcher.findChild(QLabel, "modeLauncherSectionExplore")
            simulation_title = launcher.findChild(QLabel, "modeLauncherSectionSimulations")
            self.assertIsNotNone(science_title)
            self.assertIsNotNone(explore_title)
            self.assertIsNotNone(simulation_title)
            assert science_title is not None and explore_title is not None and simulation_title is not None
            self.assertEqual(science_title.text(), "Detect & Measure")
            self.assertEqual(explore_title.text(), "Explore, Visualize & Process")
            self.assertEqual(simulation_title.text(), "Simulations")
            self.assertIs(launcher.findChild(QWidget, "modeLauncherEditorialScience"), None)
            self.assertIs(launcher.findChild(QWidget, "modeLauncherEditorialExplore"), None)
            science_card = launcher._cards_by_mode[AppMode.DIFFERENTIAL_PHOTOMETRY]  # noqa: SLF001
            explore_card = launcher._cards_by_mode[AppMode.SKY_VIEW]  # noqa: SLF001
            simulation_card = launcher._cards_by_mode[AppMode.BLACK_HOLE]  # noqa: SLF001
            science_title_pos = science_title.mapTo(launcher, science_title.rect().topLeft())
            science_card_pos = science_card.mapTo(launcher, science_card.rect().topLeft())
            explore_title_pos = explore_title.mapTo(launcher, explore_title.rect().topLeft())
            explore_card_pos = explore_card.mapTo(launcher, explore_card.rect().topLeft())
            simulation_title_pos = simulation_title.mapTo(launcher, simulation_title.rect().topLeft())
            simulation_card_pos = simulation_card.mapTo(launcher, simulation_card.rect().topLeft())
            self.assertLess(science_title_pos.y(), science_card_pos.y())
            self.assertLess(explore_title_pos.y(), explore_card_pos.y())
            self.assertLess(explore_card_pos.y(), simulation_title_pos.y())
            self.assertLess(simulation_title_pos.y(), simulation_card_pos.y())
            self.assertLess(abs(science_title_pos.x() - science_card_pos.x()), 24)
            self.assertLess(abs(explore_title_pos.x() - explore_card_pos.x()), 24)
            self.assertLess(abs(simulation_title_pos.x() - simulation_card_pos.x()), 24)
        finally:
            launcher.close()

    def test_primary_cards_are_larger_and_text_sits_below_the_image(self) -> None:
        launcher = ModeLauncherWidget()
        launcher.resize(1675, 895)
        launcher.show()
        QApplication.processEvents()
        try:
            science_card = launcher._cards_by_mode[AppMode.DIFFERENTIAL_PHOTOMETRY]  # noqa: SLF001
            explore_card = launcher._cards_by_mode[AppMode.ASTROSTACK]  # noqa: SLF001
            black_hole_card = launcher._cards_by_mode[AppMode.BLACK_HOLE]  # noqa: SLF001
            self.assertEqual(black_hole_card.width(), explore_card.width())
            self.assertGreater(science_card.width(), explore_card.width())
            self.assertGreater(science_card.height(), explore_card.height())
            self.assertLess(science_card.height(), 520)
            self.assertGreaterEqual(science_card.width(), 400)
            self.assertLessEqual(science_card.width(), 470)
            self.assertGreaterEqual(explore_card.width(), 230)
            self.assertLessEqual(explore_card.width(), 280)
            scroll = launcher._scroll_area.verticalScrollBar()  # noqa: SLF001
            self.assertEqual(scroll.maximum(), 0)
            self.assertGreater(science_card.image_band_height(), explore_card.image_band_height())
            for card in (science_card, explore_card):
                self.assertGreater(card.footer_band_height(), 36)
                self.assertGreaterEqual(card.height(), card.image_band_height() + card.footer_band_height())
            self.assertTrue(science_card.toolTip())
        finally:
            launcher.close()

    def test_launcher_emits_selected_mode(self) -> None:
        launcher = ModeLauncherWidget()
        received: list[AppMode] = []
        launcher.mode_selected.connect(received.append)
        card = launcher._cards_by_mode[AppMode.TRANSIENT_FINDER]  # noqa: SLF001
        card.click()
        self.assertEqual(received, [AppMode.TRANSIENT_FINDER])

    def test_launcher_content_max_width_is_desktop_friendly(self) -> None:
        self.assertGreaterEqual(_CONTENT_MAX_WIDTH, 1250)
        self.assertLessEqual(_CONTENT_MAX_WIDTH, 1800)
        reference = launcher_content_width(1650)
        self.assertGreaterEqual(reference, 1300)
        self.assertLessEqual(reference, 1400)

    def test_launcher_grid_breakpoints(self) -> None:
        self.assertEqual(launcher_grid_column_count(1500, tier="explore", card_count=5), 5)
        self.assertEqual(launcher_grid_column_count(1720, tier="explore", card_count=5), 5)
        self.assertEqual(launcher_grid_column_count(900, tier="explore", card_count=5), 3)
        self.assertEqual(launcher_grid_column_count(1720, tier="science"), 3)
        self.assertEqual(launcher_grid_column_count(1720, tier="explore"), 3)
        self.assertEqual(launcher_grid_column_count(700, tier="science"), 2)
        self.assertEqual(launcher_grid_column_count(480, tier="science"), 1)

    def test_launcher_emits_every_mode(self) -> None:
        launcher = ModeLauncherWidget()
        received: list[AppMode] = []
        launcher.mode_selected.connect(received.append)
        for mode in (
            AppMode.DIFFERENTIAL_PHOTOMETRY,
            AppMode.ASTEROID_COMET_DETECTION,
            AppMode.TRANSIENT_FINDER,
            AppMode.SKY_VIEW,
            AppMode.SKY_EXPLORER,
            AppMode.HR_DIAGRAM,
            AppMode.DISTANCE_MAP,
            AppMode.ASTROSTACK,
            AppMode.BLACK_HOLE,
            AppMode.GALAXY_MERGER,
            AppMode.CLUSTER_FLOW,
            AppMode.BINARY_LIGHT_LAB,
            AppMode.AURORA_MAGNETOSPHERE,
        ):
            received.clear()
            launcher._cards_by_mode[mode].click()  # noqa: SLF001
            self.assertEqual(received, [mode])


class ModeLauncherAssetResolutionTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls._app = QApplication.instance() or QApplication([])

    def test_resolve_mode_launcher_image_is_case_insensitive(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            assets_dir = Path(tmp_dir) / "assets" / "mode_launcher"
            assets_dir.mkdir(parents=True)
            image_path = assets_dir / "Sky_Explorer.png"
            image_path.write_bytes(b"placeholder")
            with patch("photometry_app.ui.mode_launcher.application_root_path", return_value=Path(tmp_dir)):
                resolved = _resolve_mode_launcher_image(("sky_explorer.png",))
            self.assertEqual(resolved, image_path)

    def test_mode_entries_reference_user_asset_names(self) -> None:
        image_names = {entry.mode: entry.image_names[0] for entry in MODE_LAUNCHER_ENTRIES}
        self.assertEqual(image_names[AppMode.DIFFERENTIAL_PHOTOMETRY], "differential_photometry.jpg")
        self.assertEqual(image_names[AppMode.ASTEROID_COMET_DETECTION], "asteroid.gif")
        self.assertEqual(image_names[AppMode.HR_DIAGRAM], "hr_diagram.png")
        self.assertEqual(image_names[AppMode.TRANSIENT_FINDER], "Transient.gif")
        self.assertEqual(image_names[AppMode.SKY_EXPLORER], "Sky_Explorer.png")
        self.assertEqual(image_names[AppMode.SKY_VIEW], "sky_atlas.jpg")
        self.assertEqual(image_names[AppMode.DISTANCE_MAP], "distance_map.png")
        self.assertEqual(image_names[AppMode.ASTROSTACK], "astrostack.gif")
        self.assertEqual(image_names[AppMode.BLACK_HOLE], "black_hole.jpg")
        self.assertEqual(image_names[AppMode.GALAXY_MERGER], "galaxy_merger.jpg")
        self.assertEqual(image_names[AppMode.CLUSTER_FLOW], "cluster_flow.jpg")

    def test_sky_atlas_subtitle_mentions_all_sky_map(self) -> None:
        sky_atlas_entry = next(entry for entry in MODE_LAUNCHER_ENTRIES if entry.mode is AppMode.SKY_VIEW)
        self.assertIn("all-sky map", sky_atlas_entry.subtitle)

    def test_draw_cover_pixmap_preserves_aspect_ratio(self) -> None:
        source = QPixmap(200, 100)
        source.fill(QColor("white"))
        scaled = source.scaled(
            QSize(100, 100),
            Qt.AspectRatioMode.KeepAspectRatioByExpanding,
            Qt.TransformationMode.SmoothTransformation,
        )
        self.assertEqual(scaled.width(), 200)
        self.assertEqual(scaled.height(), 100)

        target = QRectF(0.0, 0.0, 100.0, 100.0)
        image = QImage(100, 100, QImage.Format.Format_ARGB32)
        image.fill(Qt.GlobalColor.transparent)
        painter = QPainter(image)
        _draw_cover_pixmap(painter, source, target)
        painter.end()
        self.assertGreater(image.pixelColor(0, 50).alpha(), 0)
        self.assertGreater(image.pixelColor(99, 50).alpha(), 0)
