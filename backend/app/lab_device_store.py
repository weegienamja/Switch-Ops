"""Keyring-only credentials for explicitly configured Lab Assurance devices.

The registry persisted under ``data`` contains opaque random identifiers only.
Labels, addresses, usernames and secrets are held together in the OS keyring,
so deleting a keyring entry cannot leave an operational identity in a file.
There is deliberately no file or environment fallback.
"""
from __future__ import annotations

import json
import logging
from pathlib import Path
from threading import RLock
from uuid import uuid4

from .config import DATA_DIR
from .credential_store import KeyringCredentialVault, SwitchCredentials
from .file_security import harden_private_file
from .models import ConfiguredLabDevice, LabDeviceCreateRequest, LabDeviceList
from .logging_config import register_secret


logger = logging.getLogger(__name__)
REGISTRY_FILE = DATA_DIR / "lab-device-registry.json"


class LabDeviceStore:
    def __init__(
        self,
        registry_file: Path = REGISTRY_FILE,
        *,
        credential_vault: KeyringCredentialVault | None = None,
    ) -> None:
        self._registry_file = registry_file
        self._credential_vault = credential_vault or KeyringCredentialVault()
        self._lock = RLock()

    @property
    def keyring_available(self) -> bool:
        return self._credential_vault.available

    def _ids(self) -> list[str]:
        if not self._registry_file.exists():
            return []
        try:
            payload = json.loads(self._registry_file.read_text(encoding="utf-8"))
            values = payload.get("device_ids", [])
            return [value for value in values if isinstance(value, str) and value.startswith("lab-")]
        except Exception:
            logger.warning("Lab device registry could not be read.")
            return []

    def _write_ids(self, values: list[str]) -> None:
        self._registry_file.parent.mkdir(parents=True, exist_ok=True)
        temp = self._registry_file.with_suffix(".json.tmp")
        temp.write_text(json.dumps({"device_ids": values}, indent=2), encoding="utf-8")
        harden_private_file(temp)
        temp.replace(self._registry_file)
        harden_private_file(self._registry_file)

    def add(self, request: LabDeviceCreateRequest) -> ConfiguredLabDevice:
        if not self._credential_vault.available:
            raise RuntimeError("Windows Credential Manager is unavailable; the device was not saved.")
        device_id = f"lab-{uuid4().hex}"
        register_secret(request.password)
        register_secret(request.enable_secret)
        payload = request.model_dump(by_alias=False)
        try:
            self._credential_vault.save_lab_device(device_id, json.dumps(payload))
        except Exception as exc:
            raise RuntimeError("Windows Credential Manager refused the device credentials.") from exc
        with self._lock:
            ids = self._ids()
            ids.append(device_id)
            try:
                self._write_ids(ids)
            except Exception:
                try:
                    self._credential_vault.delete_lab_device(device_id)
                except Exception:
                    pass
                raise
        return ConfiguredLabDevice(
            id=device_id,
            label=request.label,
            primary=False,
            deviceType=request.device_type,
            storage="keyring",
            configured=True,
        )

    def _load_payload(self, device_id: str) -> dict | None:
        if not self._credential_vault.available:
            return None
        try:
            raw = self._credential_vault.load_lab_device(device_id)
            return json.loads(raw) if raw else None
        except Exception:
            return None

    def credentials(self, device_id: str) -> tuple[str, SwitchCredentials] | None:
        payload = self._load_payload(device_id)
        if not payload:
            return None
        try:
            credentials = SwitchCredentials(
                switch_host=str(payload["host"]),
                switch_username=str(payload["username"]),
                switch_password=str(payload["password"]),
                switch_enable_secret=str(payload.get("enable_secret") or ""),
                switch_device_type=str(payload.get("device_type") or "cisco_ios"),
            )
        except (KeyError, TypeError, ValueError):
            return None
        register_secret(credentials.switch_password)
        register_secret(credentials.switch_enable_secret)
        return str(payload.get("label") or "Configured IOS device"), credentials

    def list(self) -> LabDeviceList:
        devices: list[ConfiguredLabDevice] = []
        with self._lock:
            ids = self._ids()
        for device_id in ids:
            payload = self._load_payload(device_id)
            if payload:
                devices.append(
                    ConfiguredLabDevice(
                        id=device_id,
                        label=str(payload.get("label") or "Configured IOS device"),
                        primary=False,
                        deviceType=str(payload.get("device_type") or "cisco_ios"),
                        storage="keyring",
                        configured=True,
                    )
                )
            else:
                devices.append(
                    ConfiguredLabDevice(
                        id=device_id,
                        label="Unavailable keyring entry",
                        primary=False,
                        deviceType="unknown",
                        storage="none",
                        configured=False,
                    )
                )
        return LabDeviceList(keyringAvailable=self.keyring_available, devices=devices)

    def remove(self, device_id: str) -> bool:
        if device_id not in self._ids():
            return False
        if not self._credential_vault.available:
            raise RuntimeError("Windows Credential Manager is unavailable; the device was not removed.")
        try:
            self._credential_vault.delete_lab_device(device_id)
        except Exception as exc:
            raise RuntimeError("Windows Credential Manager refused to remove the device.") from exc
        with self._lock:
            self._write_ids([value for value in self._ids() if value != device_id])
        return True


_store: LabDeviceStore | None = None


def get_lab_device_store() -> LabDeviceStore:
    global _store
    if _store is None:
        _store = LabDeviceStore()
    return _store
