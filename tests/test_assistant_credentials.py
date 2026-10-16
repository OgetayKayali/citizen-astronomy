from __future__ import annotations

import os
import unittest

import keyring
from keyring.backend import KeyringBackend
from keyring.backends import fail
from keyring.errors import PasswordDeleteError

from photometry_app.assistant.credentials import (
    API_KEY_ENV_VAR,
    CREDENTIAL_SERVICE_NAME,
    CredentialStoreError,
    KeyringCredentialStore,
    api_key_account,
    delete_api_key,
    load_api_key,
    load_stored_api_key,
    save_api_key,
    set_credential_store,
)


class MemoryCredentialStore:
    def __init__(self) -> None:
        self.secrets: dict[str, str] = {}

    def get(self, account: str) -> str | None:
        return self.secrets.get(account)

    def set(self, account: str, secret: str) -> None:
        self.secrets[account] = secret

    def delete(self, account: str) -> None:
        self.secrets.pop(account, None)


class _MemoryKeyringBackend(KeyringBackend):
    priority = 1

    def __init__(self) -> None:
        super().__init__()
        self.passwords: dict[tuple[str, str], str] = {}

    def get_password(self, service: str, username: str) -> str | None:
        return self.passwords.get((service, username))

    def set_password(self, service: str, username: str, password: str) -> None:
        self.passwords[(service, username)] = password

    def delete_password(self, service: str, username: str) -> None:
        if (service, username) not in self.passwords:
            raise PasswordDeleteError("missing")
        del self.passwords[(service, username)]


class CredentialHelpersTest(unittest.TestCase):
    def setUp(self) -> None:
        self.store = MemoryCredentialStore()
        set_credential_store(self.store)
        self._previous_env = os.environ.pop(API_KEY_ENV_VAR, None)

    def tearDown(self) -> None:
        set_credential_store(None)
        os.environ.pop(API_KEY_ENV_VAR, None)
        if self._previous_env is not None:
            os.environ[API_KEY_ENV_VAR] = self._previous_env

    def test_keys_are_stored_per_provider(self) -> None:
        save_api_key("anthropic", "  sk-ant-1  ")
        save_api_key("gemini", "g-key")
        self.assertEqual(self.store.secrets[api_key_account("anthropic")], "sk-ant-1")
        self.assertEqual(load_api_key("anthropic"), "sk-ant-1")
        self.assertEqual(load_api_key("gemini"), "g-key")
        self.assertIsNone(load_api_key("openai_compatible"))

        delete_api_key("anthropic")
        self.assertIsNone(load_stored_api_key("anthropic"))
        delete_api_key("anthropic")

    def test_environment_variable_overrides_stored_key(self) -> None:
        save_api_key("gemini", "stored")
        os.environ[API_KEY_ENV_VAR] = " from-env "
        self.assertEqual(load_api_key("gemini"), "from-env")
        self.assertEqual(load_stored_api_key("gemini"), "stored")

    def test_empty_key_is_rejected(self) -> None:
        with self.assertRaises(CredentialStoreError):
            save_api_key("gemini", "   ")
        self.assertEqual(self.store.secrets, {})


class KeyringCredentialStoreTest(unittest.TestCase):
    def setUp(self) -> None:
        self._previous_keyring = keyring.get_keyring()

    def tearDown(self) -> None:
        keyring.set_keyring(self._previous_keyring)

    def test_round_trip_uses_cast_service_name(self) -> None:
        backend = _MemoryKeyringBackend()
        keyring.set_keyring(backend)
        store = KeyringCredentialStore()
        store.set(api_key_account("anthropic"), "secret")
        self.assertEqual(backend.passwords, {(CREDENTIAL_SERVICE_NAME, "ai-api-key:anthropic"): "secret"})
        self.assertEqual(store.get(api_key_account("anthropic")), "secret")
        store.delete(api_key_account("anthropic"))
        store.delete(api_key_account("anthropic"))
        self.assertEqual(backend.passwords, {})

    def test_missing_backend_raises_readable_error(self) -> None:
        keyring.set_keyring(fail.Keyring())
        store = KeyringCredentialStore()
        with self.assertRaisesRegex(CredentialStoreError, "not available"):
            store.get(api_key_account("gemini"))
        with self.assertRaisesRegex(CredentialStoreError, "not available"):
            store.set(api_key_account("gemini"), "x")


if __name__ == "__main__":
    unittest.main()
