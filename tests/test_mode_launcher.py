from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from PySide6.QtCore import QRectF, QSize, Qt
from PySide6.QtGui import QColor, QImage, QPainter, QPixmap
from PySide6.QtWidgets import QApplication, QLabel, QWidget

from photometry_app.app_metadata import APP_DISPLAY_NAME
from photometry_app.core.models import AppMode
from photometry_app.core.settings import AppSettings
from photometry_app.ui.mode_launcher import (
    EXPLORE_LEARN_ENTRIES,
    MODE_LAUNCHER_ENTRIES,
    SCIENCE_WORKFLOW_ENTRIES,
    VISUALIZATIONS_TOOLS_LABEL,
    _CONTENT_MAX_WIDTH,
    ModeLauncherWidget,
    _draw_cover_pixmap,
    _editorial_title_metrics,
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

    def test_visualizations_tools_section_label(self) -> None:
        self.assertEqual(VISUALIZATIONS_TOOLS_LABEL, "Visualizations & Tools")
        lines, _, _ = _editorial_title_metrics("explore")
        self.assertEqual(lines, ("VISUALIZATIONS", "& TOOLS"))

    def test_launcher_shows_only_app_identity_label(self) -> None:
        launcher = ModeLauncherWidget()
        identity = launcher.findChild(QLabel, "modeLauncherIdentity")
        self.assertIsNotNone(identity)
        assert identity is not None
        self.assertEqual(identity.text(), APP_DISPLAY_NAME)
        self.assertIs(launcher.findChild(QLabel, "modeLauncherHeaderTitle"), None)
        self.assertIs(launcher.findChild(QLabel, "modeLauncherHeaderSubtitle"), None)

    def test_launcher_uses_editorial_split_sections(self) -> None:
        launcher = ModeLauncherWidget()
        launcher.resize(1400, 900)
        science_title = launcher.findChild(QWidget, "modeLauncherEditorialScience")
        explore_title = launcher.findChild(QWidget, "modeLauncherEditorialExplore")
        self.assertIsNotNone(science_title)
        self.assertIsNotNone(explore_title)
        self.assertIs(launcher.findChild(QWidget, "modeLauncherScienceZone"), None)
        self.assertIs(launcher.findChild(QWidget, "modeLauncherExploreZone"), None)

    def test_launcher_fits_cards_to_viewport(self) -> None:
        launcher = ModeLauncherWidget()
        launcher.resize(1400, 900)
        QApplication.processEvents()
        science_card = launcher._cards_by_mode[AppMode.DIFFERENTIAL_PHOTOMETRY]  # noqa: SLF001
        explore_card = launcher._cards_by_mode[AppMode.ASTROSTACK]  # noqa: SLF001
        self.assertGreaterEqual(science_card.minimumHeight(), 108)
        self.assertEqual(science_card.minimumHeight(), explore_card.minimumHeight())

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

    def test_launcher_grid_breakpoints(self) -> None:
        self.assertEqual(launcher_grid_column_count(900, tier="explore", card_count=5), 5)
        self.assertEqual(launcher_grid_column_count(1720, tier="explore", card_count=5), 5)
        self.assertEqual(launcher_grid_column_count(1720, tier="science"), 3)
        self.assertEqual(launcher_grid_column_count(1720, tier="explore"), 3)
        self.assertEqual(launcher_grid_column_count(760, tier="science"), 2)
        self.assertEqual(launcher_grid_column_count(480, tier="science"), 1)


class ModeLauncherAssetResolutionTest(unittest.TestCase):
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
