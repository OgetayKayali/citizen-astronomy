"""Discord invite in About, and a once-per-session reminder that can be turned off."""

from __future__ import annotations

import re
from collections.abc import Callable

from PySide6.QtCore import QObject, Qt, QTimer, QUrl
from PySide6.QtGui import QDesktopServices, QShowEvent
from PySide6.QtWidgets import (
    QCheckBox,
    QDialog,
    QHBoxLayout,
    QLabel,
    QMessageBox,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from photometry_app.app_metadata import APP_DISCORD_INVITE_URL

# Long enough that startup is finished, short enough to appear in a normal session.
DISCORD_INVITE_DELAY_MS = 3 * 60 * 1000
DISCORD_INVITE_RETRY_MS = 60 * 1000

_URL_RE = re.compile(r"https://[^\s<]+")


def should_show_discord_invite(settings: object | None) -> bool:
    if settings is None:
        return False
    return bool(getattr(settings, "show_discord_invite", True))


def apply_discord_invite_opt_out(settings: object | None, *, opted_out: bool) -> bool:
    """Turn the reminder off. Returns True when the setting changed and should be saved."""

    if settings is None or not opted_out:
        return False
    if not bool(getattr(settings, "show_discord_invite", True)):
        return False
    setattr(settings, "show_discord_invite", False)
    return True


def start_discord_invite_timer(
    parent: QObject,
    on_timeout: Callable[[], None],
    *,
    enabled: bool,
) -> QTimer | None:
    if not enabled:
        return None
    timer = QTimer(parent)
    timer.setSingleShot(True)
    timer.setInterval(DISCORD_INVITE_DELAY_MS)
    timer.timeout.connect(on_timeout)
    timer.start()
    return timer


def message_to_linked_html(message: str) -> str:
    escaped = message.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")

    def _replace(match: re.Match[str]) -> str:
        raw = match.group(0)
        url = raw.rstrip(").,;")
        suffix = raw[len(url) :]
        return f'<a href="{url}">{url}</a>{suffix}'

    linked = _URL_RE.sub(_replace, escaped)
    return linked.replace("\n", "<br>")


def enable_message_box_links(box: QMessageBox) -> None:
    for label in box.findChildren(QLabel):
        label.setTextInteractionFlags(Qt.TextInteractionFlag.TextBrowserInteraction)
        label.setOpenExternalLinks(True)


class LinkedMessageBox(QMessageBox):
    def showEvent(self, event: QShowEvent) -> None:
        super().showEvent(event)
        enable_message_box_links(self)


def build_about_message_box(parent: QWidget | None = None) -> QMessageBox:
    from photometry_app.core.packaged_format_smoke import about_dialog_content

    title, message = about_dialog_content()
    box = LinkedMessageBox(parent)
    box.setIcon(QMessageBox.Icon.Information)
    box.setWindowTitle(title)
    box.setTextFormat(Qt.TextFormat.RichText)
    box.setText(message_to_linked_html(message))
    box.setStandardButtons(QMessageBox.StandardButton.Ok)
    return box


class DiscordInviteDialog(QDialog):
    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("discordInviteDialog")
        self.setWindowTitle("Join the CAst Discord")
        self.setMinimumWidth(460)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(18, 16, 18, 14)
        layout.setSpacing(10)

        body = QLabel(
            "We answer questions on Discord and share more detail about the app there. "
            "Join us if you want to ask something or follow along.\n\n"
            "This reminder appears once each time you open CAst, a few minutes after it starts.",
            self,
        )
        body.setObjectName("discordInviteBody")
        body.setWordWrap(True)
        layout.addWidget(body)

        link = QLabel(f'<a href="{APP_DISCORD_INVITE_URL}">{APP_DISCORD_INVITE_URL}</a>', self)
        link.setObjectName("discordInviteLink")
        link.setTextFormat(Qt.TextFormat.RichText)
        link.setTextInteractionFlags(Qt.TextInteractionFlag.TextBrowserInteraction)
        link.setOpenExternalLinks(True)
        layout.addWidget(link)

        self._opt_out = QCheckBox("Don't show this again", self)
        self._opt_out.setObjectName("discordInviteOptOut")
        layout.addWidget(self._opt_out)

        buttons = QHBoxLayout()
        buttons.addStretch(1)
        close_button = QPushButton("Close", self)
        close_button.setObjectName("discordInviteClose")
        close_button.setDefault(True)
        close_button.clicked.connect(self.reject)
        join_button = QPushButton("Join Discord", self)
        join_button.setObjectName("discordInviteJoin")
        join_button.clicked.connect(self._open_invite)
        buttons.addWidget(close_button)
        buttons.addWidget(join_button)
        layout.addLayout(buttons)

    def opt_out_checked(self) -> bool:
        return self._opt_out.isChecked()

    def _open_invite(self) -> None:
        QDesktopServices.openUrl(QUrl(APP_DISCORD_INVITE_URL))
