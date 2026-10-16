from __future__ import annotations

from dataclasses import replace

from PySide6.QtCore import QThread, Signal
from PySide6.QtWidgets import (
    QComboBox,
    QFormLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from photometry_app.assistant.config import resolve_base_url
from photometry_app.assistant.credentials import (
    API_KEY_ENV_VAR,
    CredentialStoreError,
    credential_store_label,
    delete_api_key,
    environment_api_key,
    load_stored_api_key,
    save_api_key,
)
from photometry_app.assistant.providers import (
    PROVIDER_INFOS,
    ProviderConfig,
    ProviderError,
    create_provider,
    normalize_provider_id,
    provider_info,
    provider_requires_api_key,
)
from photometry_app.core.settings import AppSettings


PRIVACY_NOTE = (
    "The assistant sends your chat messages to the provider you choose, together with the file paths, "
    "image header values, and CAst results it looks up to answer you. Image pixels are never sent. "
    "Requests are billed to your provider account."
)

# Workers outlive the Settings dialog if it closes mid-request; a running QThread must not be collected.
_running_model_workers: set[QThread] = set()


class ModelListWorker(QThread):
    models_loaded = Signal(str, object)
    load_failed = Signal(str, str)

    def __init__(self, config: ProviderConfig) -> None:
        super().__init__()
        self._config = config

    def start_kept_alive(self) -> None:
        _running_model_workers.difference_update({item for item in _running_model_workers if item.isFinished()})
        _running_model_workers.add(self)
        self.start()

    def run(self) -> None:
        try:
            models = create_provider(self._config).list_models()
        except ProviderError as exc:
            self.load_failed.emit(self._config.provider_id, str(exc))
            return
        except Exception as exc:
            self.load_failed.emit(self._config.provider_id, f"Could not load models: {exc}")
            return
        self.models_loaded.emit(self._config.provider_id, models)


class AssistantSettingsPanel(QWidget):
    def __init__(self, settings: AppSettings, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._current_provider = normalize_provider_id(settings.ai_provider)
        self._models: dict[str, str] = dict(settings.ai_models or {})
        self._base_url = str(settings.ai_openai_compatible_base_url or "")
        self._pending_keys: dict[str, str] = {}
        self._pending_removals: set[str] = set()
        self._stored_key_present: dict[str, bool] = {}
        self._stored_key_errors: dict[str, str] = {}
        self._syncing = False

        self._provider_input = QComboBox()
        for info in PROVIDER_INFOS:
            self._provider_input.addItem(info.display_name, info.provider_id)
        self._provider_input.setToolTip("Which AI service answers in the assistant chat.")

        self._base_url_input = QLineEdit()
        self._base_url_input.setClearButtonEnabled(True)
        self._base_url_input.setToolTip(
            "Address of an OpenAI-compatible server. Leave empty for OpenAI. "
            "Local servers: Ollama http://localhost:11434/v1, LM Studio http://localhost:1234/v1."
        )

        self._model_input = QComboBox()
        self._model_input.setEditable(True)
        self._model_input.setInsertPolicy(QComboBox.InsertPolicy.NoInsert)
        self._model_input.setMinimumContentsLength(28)
        self._model_input.setToolTip("Model to use. Pick one after Load Models, or type the exact model name. It must support tool calling.")
        self._load_models_button = QPushButton("Load Models")
        self._load_models_button.setToolTip("Ask the provider which models this key can use.")
        self._load_models_button.clicked.connect(self._load_models)
        model_row = QHBoxLayout()
        model_row.setContentsMargins(0, 0, 0, 0)
        model_row.addWidget(self._model_input, stretch=1)
        model_row.addWidget(self._load_models_button)

        self._api_key_input = QLineEdit()
        self._api_key_input.setEchoMode(QLineEdit.EchoMode.Password)
        self._api_key_input.setClearButtonEnabled(True)
        self._remove_key_button = QPushButton("Remove Saved Key")
        self._remove_key_button.clicked.connect(self._mark_key_for_removal)
        key_row = QHBoxLayout()
        key_row.setContentsMargins(0, 0, 0, 0)
        key_row.addWidget(self._api_key_input, stretch=1)
        key_row.addWidget(self._remove_key_button)

        self._key_status_label = QLabel()
        self._key_status_label.setWordWrap(True)
        self._model_status_label = QLabel()
        self._model_status_label.setWordWrap(True)

        self._form = QFormLayout()
        self._form.addRow("Provider", self._provider_input)
        self._form.addRow("Server Address", self._base_url_input)
        self._form.addRow("Model", model_row)
        self._form.addRow("", self._model_status_label)
        self._form.addRow("API Key", key_row)
        self._form.addRow("", self._key_status_label)

        connection_group = QGroupBox("Connection")
        connection_group.setLayout(self._form)

        privacy_label = QLabel(PRIVACY_NOTE)
        privacy_label.setWordWrap(True)
        privacy_group = QGroupBox("What is sent")
        privacy_layout = QVBoxLayout()
        privacy_layout.addWidget(privacy_label)
        privacy_group.setLayout(privacy_layout)

        layout = QVBoxLayout()
        layout.addWidget(connection_group)
        layout.addWidget(privacy_group)
        layout.addStretch(1)
        self.setLayout(layout)

        self._provider_input.currentIndexChanged.connect(self._handle_provider_changed)
        self._base_url_input.textChanged.connect(self._handle_base_url_changed)
        self._api_key_input.textChanged.connect(self._handle_key_text_changed)
        self._show_provider(self._current_provider)

    def current_provider_id(self) -> str:
        return self._current_provider

    def apply_to(self, settings: AppSettings) -> AppSettings:
        self._stash_current_fields()
        models = {provider_id: model for provider_id, model in self._models.items() if model.strip()}
        return replace(
            settings,
            ai_provider=self._current_provider,
            ai_models=models,
            ai_openai_compatible_base_url=self._base_url.strip().rstrip("/"),
        )

    def has_pending_credential_changes(self) -> bool:
        self._stash_current_fields()
        return bool(self._pending_keys or self._pending_removals)

    def commit_credentials(self) -> str | None:
        self._stash_current_fields()
        try:
            for provider_id in sorted(self._pending_removals):
                delete_api_key(provider_id)
                self._stored_key_present[provider_id] = False
            self._pending_removals.clear()
            for provider_id, api_key in sorted(self._pending_keys.items()):
                save_api_key(provider_id, api_key)
                self._stored_key_present[provider_id] = True
            self._pending_keys.clear()
        except CredentialStoreError as exc:
            self._refresh_key_status()
            return str(exc)
        self._syncing = True
        try:
            self._api_key_input.clear()
        finally:
            self._syncing = False
        self._refresh_key_status()
        return None

    def restore_defaults(self, defaults: AppSettings) -> None:
        self._models = dict(defaults.ai_models or {})
        self._base_url = str(defaults.ai_openai_compatible_base_url or "")
        self._stash_current_key_only()
        self._current_provider = normalize_provider_id(defaults.ai_provider)
        self._show_provider(self._current_provider)

    def _handle_provider_changed(self, _index: int) -> None:
        if self._syncing:
            return
        self._stash_current_fields()
        self._current_provider = normalize_provider_id(self._provider_input.currentData())
        self._show_provider(self._current_provider)

    def _handle_base_url_changed(self, text: str) -> None:
        if self._syncing:
            return
        self._base_url = text
        self._refresh_key_status()

    def _handle_key_text_changed(self, text: str) -> None:
        if self._syncing:
            return
        if text.strip():
            self._pending_keys[self._current_provider] = text.strip()
            self._pending_removals.discard(self._current_provider)
        else:
            self._pending_keys.pop(self._current_provider, None)
        self._refresh_key_status()

    def _mark_key_for_removal(self) -> None:
        provider_id = self._current_provider
        self._pending_keys.pop(provider_id, None)
        self._pending_removals.add(provider_id)
        self._syncing = True
        try:
            self._api_key_input.clear()
        finally:
            self._syncing = False
        self._refresh_key_status()

    def _stash_current_fields(self) -> None:
        model = self._model_input.currentText().strip()
        if model:
            self._models[self._current_provider] = model
        else:
            self._models.pop(self._current_provider, None)
        if provider_info(self._current_provider).base_url_editable:
            self._base_url = self._base_url_input.text()
        self._stash_current_key_only()

    def _stash_current_key_only(self) -> None:
        text = self._api_key_input.text().strip()
        if text:
            self._pending_keys[self._current_provider] = text

    def _show_provider(self, provider_id: str) -> None:
        info = provider_info(provider_id)
        self._syncing = True
        try:
            index = self._provider_input.findData(provider_id)
            self._provider_input.setCurrentIndex(max(0, index))
            self._form.setRowVisible(self._base_url_input, info.base_url_editable)
            self._base_url_input.setText(self._base_url if info.base_url_editable else "")
            self._base_url_input.setPlaceholderText(info.default_base_url)
            self._model_input.clear()
            self._model_input.lineEdit().setPlaceholderText(info.model_placeholder)
            self._model_input.setEditText(self._models.get(provider_id, ""))
            self._api_key_input.setText(self._pending_keys.get(provider_id, ""))
        finally:
            self._syncing = False
        self._model_status_label.clear()
        self._model_status_label.setVisible(False)
        self._load_stored_key_state(provider_id)
        self._refresh_key_status()

    def _load_stored_key_state(self, provider_id: str) -> None:
        if provider_id in self._stored_key_present or provider_id in self._stored_key_errors:
            return
        try:
            self._stored_key_present[provider_id] = load_stored_api_key(provider_id) is not None
        except CredentialStoreError as exc:
            self._stored_key_errors[provider_id] = str(exc)

    def _current_base_url(self) -> str:
        return resolve_base_url(self._current_provider, self._base_url)

    def _refresh_key_status(self) -> None:
        provider_id = self._current_provider
        store = credential_store_label()
        stored = self._stored_key_present.get(provider_id, False)
        pending_removal = provider_id in self._pending_removals
        typed = bool(self._pending_keys.get(provider_id))
        needs_key = provider_requires_api_key(provider_id, self._current_base_url())

        if typed:
            placeholder = ""
        elif stored and not pending_removal:
            placeholder = "Saved - type a new key to replace it"
        elif needs_key:
            placeholder = "Paste your API key"
        else:
            placeholder = "Not needed for a local server"
        self._api_key_input.setPlaceholderText(placeholder)
        self._remove_key_button.setEnabled(stored and not pending_removal)

        if environment_api_key():
            status = f"Using the key from the {API_KEY_ENV_VAR} environment variable. It overrides any saved key."
        elif provider_id in self._stored_key_errors:
            status = self._stored_key_errors[provider_id]
        elif typed:
            status = f"The new key will be saved in {store} when you click Save."
        elif pending_removal:
            status = f"The saved key will be removed from {store} when you click Save."
        elif stored:
            status = f"A key is saved in {store}."
        elif needs_key:
            status = f"No key saved. Keys are stored in {store}, not in the settings file."
        else:
            status = "No key needed for a local server."
        self._key_status_label.setText(status)

    def _load_models(self) -> None:
        self._stash_current_fields()
        provider_id = self._current_provider
        api_key = self._pending_keys.get(provider_id)
        if api_key is None and provider_id not in self._pending_removals:
            api_key = environment_api_key()
            if api_key is None:
                try:
                    api_key = load_stored_api_key(provider_id)
                except CredentialStoreError as exc:
                    self._show_model_status(str(exc))
                    return
        config = ProviderConfig(
            provider_id=provider_id,
            model=self._models.get(provider_id, ""),
            api_key=api_key,
            base_url=self._current_base_url(),
        )
        worker = ModelListWorker(config)
        worker.models_loaded.connect(self._handle_models_loaded)
        worker.load_failed.connect(self._handle_models_failed)
        self._load_models_button.setEnabled(False)
        self._show_model_status("Loading models...")
        worker.start_kept_alive()

    def _handle_models_loaded(self, provider_id: str, models: object) -> None:
        self._load_models_button.setEnabled(True)
        if provider_id != self._current_provider:
            return
        names = [str(name) for name in models] if isinstance(models, list) else []
        current = self._model_input.currentText().strip()
        self._syncing = True
        try:
            self._model_input.clear()
            self._model_input.addItems(names)
            self._model_input.setEditText(current)
        finally:
            self._syncing = False
        if names:
            self._show_model_status(f"{len(names)} models available. Pick one that supports tool calling.")
        else:
            self._show_model_status("The provider returned no models for this key.")

    def _handle_models_failed(self, provider_id: str, message: str) -> None:
        self._load_models_button.setEnabled(True)
        if provider_id == self._current_provider:
            self._show_model_status(message)

    def _show_model_status(self, text: str) -> None:
        self._model_status_label.setText(text)
        self._model_status_label.setVisible(bool(text))
