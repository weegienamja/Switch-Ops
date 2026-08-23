"""Windows Credential Manager storage for the Meraki Dashboard API key.

Unlike the legacy IOS credential store this class has no file or environment
fallback. A Dashboard API key is accepted only when the OS keyring is usable.
"""
from __future__ import annotations

import logging
import os
from typing import Any

from .logging_config import register_secret


logger = logging.getLogger(__name__)
SERVICE_NAME = "switchops-meraki"
ACCOUNT_NAME = "dashboard-api-key"


def _load_keyring() -> Any | None:
    try:
        import keyring  # type: ignore

        backend = keyring.get_keyring()
        if float(getattr(backend, "priority", 0)) <= 0:
            return None
        if os.name != "nt" or type(backend).__module__ != "keyring.backends.Windows":
            logger.info("Meraki credentials require Windows Credential Manager")
            return None
        return keyring
    except Exception as exc:  # pragma: no cover - environment dependent
        logger.info("Meraki OS keyring unavailable: %s", type(exc).__name__)
        return None


class MerakiCredentialStore:
    def __init__(self, keyring_module: Any | None = None) -> None:
        self._keyring = keyring_module if keyring_module is not None else _load_keyring()

    def available(self) -> bool:
        return self._keyring is not None

    def status(self) -> dict[str, object]:
        return {
            "configured": self.load() is not None,
            "keyring_available": self.available(),
            "storage": "keyring" if self.load() is not None else "none",
        }

    def save(self, api_key: str) -> None:
        if self._keyring is None:
            raise RuntimeError("Windows Credential Manager is unavailable.")
        register_secret(api_key)
        try:
            self._keyring.set_password(SERVICE_NAME, ACCOUNT_NAME, api_key)
        except Exception as exc:  # pragma: no cover - environment dependent
            logger.warning("Meraki keyring save failed: %s", type(exc).__name__)
            raise RuntimeError("Windows Credential Manager could not store the API key.") from None

    def load(self) -> str | None:
        if self._keyring is None:
            return None
        try:
            value = self._keyring.get_password(SERVICE_NAME, ACCOUNT_NAME)
        except Exception:  # pragma: no cover - environment dependent
            return None
        if value:
            register_secret(value)
            return str(value)
        return None

    def clear(self) -> None:
        if self._keyring is None:
            return
        try:
            self._keyring.delete_password(SERVICE_NAME, ACCOUNT_NAME)
        except Exception:
            pass


_store: MerakiCredentialStore | None = None


def get_meraki_credential_store() -> MerakiCredentialStore:
    global _store
    if _store is None:
        _store = MerakiCredentialStore()
    return _store
