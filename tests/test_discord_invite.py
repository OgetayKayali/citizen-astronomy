from __future__ import annotations

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import tempfile
import unittest
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from PySide6.QtCore import QUrl
from PySide6.QtWidgets import QApplication, QCheckBox, QDialog, QLabel, QPushButton, QWidget

from photometry_app.app_metadata import APP_DISCORD_INVITE_URL
from photometry_app.core.settings import AppSettings
from photometry_app.ui.discord_invite import (
    DISCORD_INVITE_DELAY_MS,
    DiscordInviteDialog,
    apply_discord_invite_opt_out,
    message_to_linked_html,
    should_show_discord_invite,
    start_discord_invite_timer,
)
from photometry_app.ui.main_window import MainWindow


class DiscordInviteLogicTest(unittest.TestCase):
    def test_reminder_is_on_until_the_user_turns_it_off(self) -> None:
        self.assertFalse(should_show_discord_invite(None))
        self.assertTrue(should_show_discord_invite(SimpleNamespace()))
        self.assertTrue(should_show_discord_invite(SimpleNamespace(show_discord_invite=True)))
        self.assertFalse(should_show_discord_invite(SimpleNamespace(show_discord_invite=False)))

    def test_opt_out_is_saved_only_when_the_box_is_checked(self) -> None:
        settings = SimpleNamespace(show_discord_invite=True)
        self.assertFalse(apply_discord_invite_opt_out(settings, opted_out=False))
        self.assertTrue(settings.show_discord_invite)
        self.assertTrue(apply_discord_invite_opt_out(settings, opted_out=True))
        self.assertFalse(settings.show_discord_invite)
        self.assertFalse(apply_discord_invite_opt_out(settings, opted_out=True))
        self.assertFalse(apply_discord_invite_opt_out(None, opted_out=True))

    def test_about_links_keep_the_visible_sentence_and_drop_trailing_punctuation(self) -> None:
        html = message_to_linked_html(
            "Visit https://palaz.se).\nJoin https://discord.gg/w5dxBQ5Fj"
        )
        self.assertIn('<a href="https://palaz.se">https://palaz.se</a>).', html)
        self.assertIn('<a href="https://discord.gg/w5dxBQ5Fj">https://discord.gg/w5dxBQ5Fj</a>', html)
        self.assertIn("<br>", html)
        self.assertNotIn("\n", html)

    def test_timer_fires_once_after_a_few_minutes(self) -> None:
        _app = QApplication.instance() or QApplication([])
        parent = QWidget()
        calls: list[int] = []
        timer = start_discord_invite_timer(parent, lambda: calls.append(1), enabled=True)
        self.assertIsNotNone(timer)
        assert timer is not None
        self.assertTrue(timer.isSingleShot())
        self.assertTrue(timer.isActive())
        self.assertEqual(timer.interval(), DISCORD_INVITE_DELAY_MS)
        self.assertEqual(DISCORD_INVITE_DELAY_MS, 3 * 60 * 1000)
        self.assertIsNone(start_discord_invite_timer(parent, lambda: None, enabled=False))
        timer.stop()
        parent.deleteLater()
        self.assertEqual(calls, [])


class DiscordInviteDialogTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls._app = QApplication.instance() or QApplication([])

    def test_join_opens_discord_without_closing_or_opting_out(self) -> None:
        dialog = DiscordInviteDialog()
        link = dialog.findChild(QLabel, "discordInviteLink")
        self.assertIsNotNone(link)
        assert link is not None
        self.assertIn(APP_DISCORD_INVITE_URL, link.text())
        self.assertTrue(link.openExternalLinks())
        join = dialog.findChild(QPushButton, "discordInviteJoin")
        self.assertIsNotNone(join)
        assert join is not None
        with patch("photometry_app.ui.discord_invite.QDesktopServices.openUrl") as open_url:
            join.click()
        open_url.assert_called_once()
        opened = open_url.call_args.args[0]
        self.assertIsInstance(opened, QUrl)
        self.assertEqual(opened.toString(), APP_DISCORD_INVITE_URL)
        self.assertEqual(dialog.result(), 0)
        self.assertFalse(dialog.opt_out_checked())
        dialog.deleteLater()

    def test_close_keeps_the_opt_out_choice(self) -> None:
        dialog = DiscordInviteDialog()
        opt_out = dialog.findChild(QCheckBox, "discordInviteOptOut")
        self.assertIsNotNone(opt_out)
        assert opt_out is not None
        self.assertFalse(opt_out.isChecked())
        opt_out.setChecked(True)
        self.assertTrue(dialog.opt_out_checked())
        close = dialog.findChild(QPushButton, "discordInviteClose")
        self.assertIsNotNone(close)
        assert close is not None
        close.click()
        self.assertEqual(dialog.result(), QDialog.DialogCode.Rejected)
        self.assertTrue(dialog.opt_out_checked())
        dialog.deleteLater()


class DiscordInviteWindowTest(unittest.TestCase):
    def test_schedule_passes_the_saved_choice(self) -> None:
        window = MainWindow.__new__(MainWindow)
        window._settings = SimpleNamespace(show_discord_invite=False)
        with patch("photometry_app.ui.discord_invite.start_discord_invite_timer", return_value=None) as start:
            MainWindow._schedule_discord_invite(window)
        start.assert_called_once()
        self.assertIs(start.call_args.args[0], window)
        self.assertIs(start.call_args.args[1].__func__, MainWindow._show_discord_invite)
        self.assertFalse(start.call_args.kwargs["enabled"])

    def test_finish_saves_when_the_box_is_checked(self) -> None:
        _app = QApplication.instance() or QApplication([])
        window = MainWindow.__new__(MainWindow)
        window._settings = SimpleNamespace(show_discord_invite=True)
        dialog = DiscordInviteDialog()
        opt_out = dialog.findChild(QCheckBox, "discordInviteOptOut")
        assert opt_out is not None
        opt_out.setChecked(True)
        window._discord_invite_dialog = dialog
        saved: list[bool] = []
        window._save_settings_snapshot = lambda: saved.append(window._settings.show_discord_invite)
        MainWindow._finish_discord_invite(window)
        self.assertFalse(window._settings.show_discord_invite)
        self.assertEqual(saved, [False])
        self.assertIsNone(window._discord_invite_dialog)

    def test_finish_does_not_save_when_the_box_is_clear(self) -> None:
        _app = QApplication.instance() or QApplication([])
        window = MainWindow.__new__(MainWindow)
        window._settings = SimpleNamespace(show_discord_invite=True)
        dialog = DiscordInviteDialog()
        window._discord_invite_dialog = dialog
        window._save_settings_snapshot = lambda: (_ for _ in ()).throw(AssertionError("should not save"))
        MainWindow._finish_discord_invite(window)
        self.assertTrue(window._settings.show_discord_invite)
        self.assertIsNone(window._discord_invite_dialog)


class DiscordInviteSettingsRoundTripTest(unittest.TestCase):
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

    def test_opt_out_survives_reload(self) -> None:
        root = Path(self._config_dir.name)
        settings = replace(AppSettings.defaults(root), show_discord_invite=False)
        settings.save(root)
        self.assertFalse(AppSettings.from_root(root).show_discord_invite)
