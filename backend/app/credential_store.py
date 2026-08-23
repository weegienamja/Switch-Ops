"""Local credential storage.

Order of preference:
1. OS keyring (Windows Credential Manager / macOS Keychain / Secret Service).
2. ``backend/data/credentials.json`` — guarded local file (git-ignored).
3. Environment variables (dev only; never written to disk by us).
"""
from __future__ import annotations

import json
import logging
import os
from dataclasses import dataclass, asdict
from pathlib import Path
from typing import Literal, Optional

from .config import DATA_DIR
from .file_security import harden_private_file
from .logging_config import register_secret

logger = logging.getLogger(__name__)

SERVICE_NAME = "switchops"
CRED_FILE = DATA_DIR / "credentials.json"

_ACCOUNT_KEYS = (
    "switch_host",
    "switch_username",
    "switch_password",
    "switch_enable_secret",
    "switch_device_type",
)

Storage = Literal["keyring", "file", "env", "none"]


@dataclass
class SwitchCredentials:
    switch_host: str
    switch_username: str
    switch_password: str
    switch_enable_secret: str
    switch_device_type: str = "cisco_ios"


def _try_import_keyring():
    try:
        import keyring  # type: ignore

        # Probe the backend; if it raises, fall back.
        _ = keyring.get_keyring()
        return keyring
    except Exception as exc:  # pragma: no cover - environment dependent
        logger.info("keyring unavailable: %s", type(exc).__name__)
        return None


class CredentialStore:
    def __init__(self) -> None:
        self._keyring = _try_import_keyring()

    # --- public API ------------------------------------------------------

    def storage_backend(self) -> Storage:
        if self._credentials_complete(self._load_keyring()):
            return "keyring"
        if self._credentials_complete(self._load_file()):
            return "file"
        if self._credentials_complete(self._load_env()):
            return "env"
        return "none"

    def is_configured(self) -> bool:
        return self.storage_backend() != "none"

    def status(self) -> dict:
        backend = self.storage_backend()
        creds = self.load(safe=True) if backend != "none" else None
        return {
            "configured": backend != "none",
            "has_password": bool(creds and creds.switch_password),
            "has_enable_secret": bool(creds and creds.switch_enable_secret),
            "storage": backend,
            "switch_host": creds.switch_host if creds else None,
            "switch_username": creds.switch_username if creds else None,
            "switch_device_type": creds.switch_device_type if creds else None,
        }

    def save(self, creds: SwitchCredentials) -> Storage:
        if not self._credentials_complete(creds):
            raise ValueError("Host, username, and password are required.")
        register_secret(creds.switch_password)
        register_secret(creds.switch_enable_secret)
        if self._keyring is not None:
            try:
                self._save_keyring(creds)
                # Remove any leftover file fallback for hygiene.
                if CRED_FILE.exists():
                    CRED_FILE.unlink()
                return "keyring"
            except Exception as exc:  # pragma: no cover - environment dependent
                logger.warning("keyring save failed: %s", type(exc).__name__)
        self._save_file(creds)
        return "file"

    def load(self, *, safe: bool = False) -> Optional[SwitchCredentials]:
        """Load credentials. If ``safe``, return placeholders for secrets."""
        creds = next(
            (
                candidate
                for candidate in (
                    self._load_keyring(),
                    self._load_file(),
                    self._load_env(),
                )
                if self._credentials_complete(candidate)
            ),
            None,
        )
        if creds is None:
            return None
        register_secret(creds.switch_password)
        register_secret(creds.switch_enable_secret)
        if safe:
            return SwitchCredentials(
                switch_host=creds.switch_host,
                switch_username=creds.switch_username,
                switch_password="***" if creds.switch_password else "",
                switch_enable_secret="***" if creds.switch_enable_secret else "",
                switch_device_type=creds.switch_device_type,
            )
        return creds

    def clear(self) -> None:
        if self._keyring is not None:
            for key in _ACCOUNT_KEYS:
                try:
                    self._keyring.delete_password(SERVICE_NAME, key)
                except Exception:
                    pass
        if CRED_FILE.exists():
            try:
                CRED_FILE.unlink()
            except Exception:  # pragma: no cover
                pass

    # --- keyring ---------------------------------------------------------

    @staticmethod
    def _credentials_complete(creds: Optional[SwitchCredentials]) -> bool:
        return bool(
            creds
            and creds.switch_host.strip()
            and creds.switch_username.strip()
            and creds.switch_password
        )

    def _save_keyring(self, creds: SwitchCredentials) -> None:
        assert self._keyring is not None
        d = asdict(creds)
        for k in _ACCOUNT_KEYS:
            v = d.get(k) or ""
            self._keyring.set_password(SERVICE_NAME, k, v)

    def _load_keyring(self) -> Optional[SwitchCredentials]:
        if self._keyring is None:
            return None
        try:
            pwd = self._keyring.get_password(SERVICE_NAME, "switch_password")
            if not pwd:
                return None
            return SwitchCredentials(
                switch_host=self._keyring.get_password(SERVICE_NAME, "switch_host") or "",
                switch_username=self._keyring.get_password(SERVICE_NAME, "switch_username") or "",
                switch_password=pwd,
                switch_enable_secret=self._keyring.get_password(SERVICE_NAME, "switch_enable_secret") or "",
                switch_device_type=self._keyring.get_password(SERVICE_NAME, "switch_device_type") or "cisco_ios",
            )
        except Exception:  # pragma: no cover
            return None

    # --- file ------------------------------------------------------------

    def _save_file(self, creds: SwitchCredentials) -> None:
        CRED_FILE.parent.mkdir(parents=True, exist_ok=True)
        temp_path = CRED_FILE.with_suffix(CRED_FILE.suffix + ".tmp")
        temp_path.write_text(json.dumps(asdict(creds), indent=2), encoding="utf-8")
        harden_private_file(temp_path)
        try:
            temp_path.replace(CRED_FILE)
            harden_private_file(CRED_FILE)
        finally:
            if temp_path.exists():
                temp_path.unlink()

    def _load_file(self) -> Optional[SwitchCredentials]:
        if not CRED_FILE.exists():
            return None
        try:
            data = json.loads(CRED_FILE.read_text(encoding="utf-8"))
            return SwitchCredentials(
                switch_host=data.get("switch_host", ""),
                switch_username=data.get("switch_username", ""),
                switch_password=data.get("switch_password", ""),
                switch_enable_secret=data.get("switch_enable_secret", ""),
                switch_device_type=data.get("switch_device_type", "cisco_ios"),
            )
        except Exception:
            logger.warning("failed to read credentials file")
            return None

    def _load_env(self) -> Optional[SwitchCredentials]:
        pwd = os.environ.get("SWITCH_PASSWORD")
        if not pwd or pwd.startswith("__REPLACE"):
            return None
        host = os.environ.get("SWITCH_HOST", "").strip()
        username = os.environ.get("SWITCH_USERNAME", "").strip()
        if not host or not username:
            return None
        return SwitchCredentials(
            switch_host=host,
            switch_username=username,
            switch_password=pwd,
            switch_enable_secret=os.environ.get("SWITCH_ENABLE_SECRET", "") or "",
            switch_device_type=os.environ.get("SWITCH_DEVICE_TYPE", "cisco_ios"),
        )


_store: Optional[CredentialStore] = None


def get_credential_store() -> CredentialStore:
    global _store
    if _store is None:
        _store = CredentialStore()
    return _store
