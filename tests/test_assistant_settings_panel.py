from __future__ import annotations

import os
from pathlib import Path
import tempfile
import time
import unittest
from unittest import mock

from PySide6.QtWidgets import QApplication

from photometry_app.assistant.credentials import API_KEY_ENV_VAR, CredentialStoreError, api_key_account, set_credential_store
from photometry_app.core.settings import AppSettings
from photometry_app.ui import assistant_settings
from photometry_app.ui.assistant_settings import AssistantSettingsPanel

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")


class MemoryCredentialStore:
    def __init__(self) -> None:
        self.secrets: dict[str, str] = {}
        self.fail_writes = False

    def get(self, account: str) -> str | None:
        return self.secrets.get(account)

    def set(self, account: str, secret: str) -> None:
        if self.fail_writes:
            raise CredentialStoreError("Could not save to Windows Credential Manager: access denied")
        self.secrets[account] = secret

    def delete(self, account: str) -> None:
        self.secrets.pop(account, None)


class _FakeModelProvider:
    def list_models(self) -> list[str]:
        return ["model-a", "model-b"]


class AssistantSettingsPanelTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls._app = QApplication.instance() or QApplication([])

    def setUp(self) -> None:
        self._config_dir = tempfile.TemporaryDirectory()
        self._previous_config_path = os.environ.get("CITIZEN_PHOTOMETRY_CONFIG_PATH")
        os.environ["CITIZEN_PHOTOMETRY_CONFIG_PATH"] = str(Path(self._config_dir.name) / "settings.json")
        self._previous_env_key = os.environ.pop(API_KEY_ENV_VAR, None)
        self.store = MemoryCredentialStore()
        set_credential_store(self.store)
        self.settings = AppSettings.from_root(Path(self._config_dir.name))

    def tearDown(self) -> None:
        set_credential_store(None)
        if self._previous_config_path is None:
            os.environ.pop("CITIZEN_PHOTOMETRY_CONFIG_PATH", None)
        else:
            os.environ["CITIZEN_PHOTOMETRY_CONFIG_PATH"] = self._previous_config_path
        if self._previous_env_key is not None:
            os.environ[API_KEY_ENV_VAR] = self._previous_env_key
        self._config_dir.cleanup()

    def _select_provider(self, panel: AssistantSettingsPanel, provider_id: str) -> None:
        panel._provider_input.setCurrentIndex(panel._provider_input.findData(provider_id))

    def test_typed_key_is_saved_to_credential_store_only_on_commit(self) -> None:
        panel = AssistantSettingsPanel(self.settings)
        self._select_provider(panel, "anthropic")
        panel._api_key_input.setText(" sk-ant-123 ")
        self.assertIn("will be saved", panel._key_status_label.text())
        self.assertEqual(self.store.secrets, {})

        built = panel.apply_to(self.settings)
        self.assertFalse(hasattr(built, "ai_api_key"))
        self.assertIsNone(panel.commit_credentials())
        self.assertEqual(self.store.secrets, {api_key_account("anthropic"): "sk-ant-123"})
        self.assertEqual(panel._api_key_input.text(), "")
        self.assertIn("A key is saved", panel._key_status_label.text())
        self.assertTrue(panel._remove_key_button.isEnabled())

    def test_models_and_keys_are_kept_per_provider(self) -> None:
        panel = AssistantSettingsPanel(self.settings)
        self._select_provider(panel, "anthropic")
        panel._model_input.setEditText("claude-test")
        panel._api_key_input.setText("ant-key")
        self._select_provider(panel, "gemini")
        self.assertEqual(panel._model_input.currentText(), "")
        self.assertEqual(panel._api_key_input.text(), "")
        self.assertFalse(panel._form.isRowVisible(panel._base_url_input))
        panel._model_input.setEditText("gemini-test")
        panel._api_key_input.setText("gem-key")
        self._select_provider(panel, "anthropic")
        self.assertEqual(panel._model_input.currentText(), "claude-test")
        self.assertEqual(panel._api_key_input.text(), "ant-key")

        built = panel.apply_to(self.settings)
        self.assertEqual(built.ai_provider, "anthropic")
        self.assertEqual(built.ai_models, {"anthropic": "claude-test", "gemini": "gemini-test"})
        self.assertIsNone(panel.commit_credentials())
        self.assertEqual(
            self.store.secrets,
            {api_key_account("anthropic"): "ant-key", api_key_account("gemini"): "gem-key"},
        )

    def test_remove_saved_key_deletes_on_commit(self) -> None:
        self.store.secrets[api_key_account("gemini")] = "old"
        self.settings.ai_provider = "gemini"
        panel = AssistantSettingsPanel(self.settings)
        self.assertIn("A key is saved", panel._key_status_label.text())
        panel._remove_key_button.click()
        self.assertIn("will be removed", panel._key_status_label.text())
        self.assertIn(api_key_account("gemini"), self.store.secrets)
        self.assertIsNone(panel.commit_credentials())
        self.assertEqual(self.store.secrets, {})
        self.assertFalse(panel._remove_key_button.isEnabled())

    def test_store_failure_is_reported_and_key_stays_pending(self) -> None:
        self.store.fail_writes = True
        panel = AssistantSettingsPanel(self.settings)
        panel._api_key_input.setText("sk-1")
        error = panel.commit_credentials()
        self.assertIn("access denied", error or "")
        self.assertTrue(panel.has_pending_credential_changes())

    def test_local_server_does_not_need_a_key(self) -> None:
        panel = AssistantSettingsPanel(self.settings)
        self.assertTrue(panel._form.isRowVisible(panel._base_url_input))
        self.assertIn("No key saved", panel._key_status_label.text())
        panel._base_url_input.setText("http://localhost:11434/v1")
        self.assertEqual(panel._key_status_label.text(), "No key needed for a local server.")
        self.assertEqual(panel.apply_to(self.settings).ai_openai_compatible_base_url, "http://localhost:11434/v1")

    def test_environment_key_is_announced(self) -> None:
        os.environ[API_KEY_ENV_VAR] = "env-key"
        try:
            panel = AssistantSettingsPanel(self.settings)
            self.assertIn(API_KEY_ENV_VAR, panel._key_status_label.text())
        finally:
            os.environ.pop(API_KEY_ENV_VAR, None)

    def test_restore_defaults_resets_provider_and_models(self) -> None:
        self.settings.ai_provider = "gemini"
        self.settings.ai_models = {"gemini": "gemini-test"}
        panel = AssistantSettingsPanel(self.settings)
        panel.restore_defaults(AppSettings.defaults(Path(self._config_dir.name)))
        built = panel.apply_to(self.settings)
        self.assertEqual(built.ai_provider, "openai_compatible")
        self.assertEqual(built.ai_models, {})

    def test_load_models_fills_the_model_list_in_the_background(self) -> None:
        panel = AssistantSettingsPanel(self.settings)
        panel._api_key_input.setText("sk-1")
        panel._model_input.setEditText("model-b")
        with mock.patch.object(assistant_settings, "create_provider", return_value=_FakeModelProvider()) as factory:
            panel._load_models()
            deadline = time.monotonic() + 5.0
            while not panel._load_models_button.isEnabled() and time.monotonic() < deadline:
                QApplication.processEvents()
                time.sleep(0.01)
        config = factory.call_args.args[0]
        self.assertEqual(config.api_key, "sk-1")
        self.assertEqual([panel._model_input.itemText(i) for i in range(panel._model_input.count())], ["model-a", "model-b"])
        self.assertEqual(panel._model_input.currentText(), "model-b")
        self.assertIn("2 models available", panel._model_status_label.text())


if __name__ == "__main__":
    unittest.main()
