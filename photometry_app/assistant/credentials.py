from __future__ import annotations

import os
import sys
from typing import Protocol


API_KEY_ENV_VAR = "CITIZEN_PHOTOMETRY_AI_API_KEY"
CREDENTIAL_SERVICE_NAME = "Citizen Astronomy (CAst)"


class CredentialStoreError(RuntimeError):
    pass


class CredentialStore(Protocol):
    def get(self, account: str) -> str | None: ...

    def set(self, account: str, secret: str) -> None: ...

    def delete(self, account: str) -> None: ...


class KeyringCredentialStore:
    def __init__(self, service_name: str = CREDENTIAL_SERVICE_NAME) -> None:
        self._service_name = service_name

    def get(self, account: str) -> str | None:
        keyring = self._keyring()
        try:
            return keyring.get_password(self._service_name, account)
        except Exception as exc:
            raise CredentialStoreError(f"Could not read from {credential_store_label()}: {exc}") from exc

    def set(self, account: str, secret: str) -> None:
        keyring = self._keyring()
        try:
            keyring.set_password(self._service_name, account, secret)
        except Exception as exc:
            raise CredentialStoreError(f"Could not save to {credential_store_label()}: {exc}") from exc

    def delete(self, account: str) -> None:
        keyring = self._keyring()
        from keyring.errors import PasswordDeleteError

        try:
            keyring.delete_password(self._service_name, account)
        except PasswordDeleteError:
            return
        except Exception as exc:
            raise CredentialStoreError(f"Could not remove the key from {credential_store_label()}: {exc}") from exc

    def _keyring(self):
        try:
            import keyring
            from keyring.backends import fail
        except Exception as exc:
            raise CredentialStoreError(f"{credential_store_label()} is not available: {exc}") from exc
        if isinstance(keyring.get_keyring(), fail.Keyring):
            raise CredentialStoreError(f"{credential_store_label()} is not available on this system.")
        return keyring


_store_override: CredentialStore | None = None
_default_store: CredentialStore | None = None


def credential_store() -> CredentialStore:
    global _default_store
    if _store_override is not None:
        return _store_override
    if _default_store is None:
        _default_store = KeyringCredentialStore()
    return _default_store


def set_credential_store(store: CredentialStore | None) -> None:
    global _store_override
    _store_override = store


def credential_store_label() -> str:
    if sys.platform == "win32":
        return "Windows Credential Manager"
    if sys.platform == "darwin":
        return "the macOS Keychain"
    return "the system keyring"


def api_key_account(provider_id: str) -> str:
    return f"ai-api-key:{provider_id}"


def environment_api_key() -> str | None:
    value = os.getenv(API_KEY_ENV_VAR, "").strip()
    return value or None


def load_stored_api_key(provider_id: str) -> str | None:
    value = credential_store().get(api_key_account(provider_id))
    value = (value or "").strip()
    return value or None


def load_api_key(provider_id: str) -> str | None:
    return environment_api_key() or load_stored_api_key(provider_id)


def save_api_key(provider_id: str, api_key: str) -> None:
    value = api_key.strip()
    if not value:
        raise CredentialStoreError("The API key is empty.")
    credential_store().set(api_key_account(provider_id), value)


def delete_api_key(provider_id: str) -> None:
    credential_store().delete(api_key_account(provider_id))
