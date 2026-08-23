"""Validated, local-only interface safety policy.

The policy contains no credentials or clear-text device address. Devices are
identified by a one-way digest of their configured host. Missing devices and
interfaces are always UNMANAGED, and any load/validation error fails closed.
"""
from __future__ import annotations

import hashlib
import json
import threading
from contextlib import contextmanager
from pathlib import Path
from typing import Iterable, Iterator, Literal

from .command_registry import (
    assert_interface_writable,
    is_physical_interface,
    normalize_interface,
    short_interface,
)
from .config import DATA_DIR
from .errors import CommandNotAllowedError, ProtectedInterfaceError, WriteActionsDisabledError
from .file_security import harden_private_file


PolicyState = Literal["PROTECTED", "OPERABLE", "UNMANAGED"]
POLICY_FILE = DATA_DIR / "device-interface-policy.json"
SCHEMA_VERSION = 1
_STATES = {"PROTECTED", "OPERABLE", "UNMANAGED"}


def device_key(host: str) -> str:
    normalized = host.strip().lower().encode("utf-8")
    return hashlib.sha256(normalized).hexdigest()


class InterfacePolicyStore:
    def __init__(self, path: Path = POLICY_FILE) -> None:
        self.path = path
        self._lock = threading.RLock()

    @staticmethod
    def _empty() -> dict:
        return {
            "schemaVersion": SCHEMA_VERSION,
            "controlledWritesEnabled": False,
            "devices": {},
        }

    def _load_unlocked(self) -> tuple[dict, str | None]:
        if not self.path.exists():
            return self._empty(), None
        try:
            raw = json.loads(self.path.read_text(encoding="utf-8"))
            if not isinstance(raw, dict) or raw.get("schemaVersion") != SCHEMA_VERSION:
                raise ValueError("unsupported policy schema")
            if not isinstance(raw.get("controlledWritesEnabled"), bool):
                raise ValueError("invalid controlled-write preference")
            devices = raw.get("devices")
            if not isinstance(devices, dict):
                raise ValueError("invalid device policy collection")
            validated: dict[str, dict[str, str]] = {}
            for key, device in devices.items():
                if not isinstance(key, str) or not _is_device_key(key):
                    raise ValueError("invalid device key")
                if not isinstance(device, dict) or not isinstance(device.get("interfaces"), dict):
                    raise ValueError("invalid device policy")
                interfaces: dict[str, str] = {}
                for interface, state in device["interfaces"].items():
                    canonical = normalize_interface(str(interface))
                    if state not in _STATES:
                        raise ValueError("invalid interface policy state")
                    if state == "OPERABLE" and not is_physical_interface(canonical):
                        raise ValueError("non-physical interface cannot be operable")
                    interfaces[canonical] = state
                validated[key] = {"interfaces": interfaces}
            return {
                "schemaVersion": SCHEMA_VERSION,
                "controlledWritesEnabled": raw["controlledWritesEnabled"],
                "devices": validated,
            }, None
        except Exception as exc:
            return self._empty(), type(exc).__name__

    def _write_unlocked(self, data: dict) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temp = self.path.with_suffix(self.path.suffix + ".tmp")
        temp.write_text(json.dumps(data, indent=2, sort_keys=True), encoding="utf-8")
        harden_private_file(temp)
        try:
            temp.replace(self.path)
            harden_private_file(self.path)
        finally:
            if temp.exists():
                temp.unlink()

    def status(self, host: str | None, observed: Iterable[str] = ()) -> dict:
        with self._lock:
            data, load_error = self._load_unlocked()
            states: dict[str, PolicyState] = {}
            key = device_key(host) if host else None
            stored = (
                data["devices"].get(key, {}).get("interfaces", {})
                if key and load_error is None
                else {}
            )
            names = set(stored)
            for interface in observed:
                try:
                    names.add(normalize_interface(interface))
                except CommandNotAllowedError:
                    continue
            for canonical in sorted(names):
                states[short_interface(canonical)] = stored.get(canonical, "UNMANAGED")
            return {
                "deviceConfigured": bool(host),
                "deviceKey": key,
                "valid": load_error is None,
                "loadError": "Policy could not be validated; writes are disabled." if load_error else None,
                "controlledWritesEnabled": bool(
                    data["controlledWritesEnabled"] and load_error is None
                ),
                "interfaces": states,
            }

    def controlled_writes_enabled(self) -> bool:
        with self._lock:
            data, load_error = self._load_unlocked()
            return load_error is None and bool(data["controlledWritesEnabled"])

    def set_controlled_writes(self, enabled: bool) -> None:
        with self._lock:
            data, load_error = self._load_unlocked()
            if load_error is not None:
                raise CommandNotAllowedError(
                    "The local interface policy is invalid. Repair or remove it before enabling writes."
                )
            data["controlledWritesEnabled"] = bool(enabled)
            self._write_unlocked(data)

    def state_for(self, host: str | None, interface: str) -> PolicyState:
        if not host:
            return "UNMANAGED"
        canonical = normalize_interface(interface)
        with self._lock:
            data, load_error = self._load_unlocked()
            if load_error is not None:
                return "UNMANAGED"
            state = (
                data["devices"]
                .get(device_key(host), {})
                .get("interfaces", {})
                .get(canonical, "UNMANAGED")
            )
            return state  # type: ignore[return-value]

    def set_state(self, host: str, interface: str, state: PolicyState) -> None:
        if not host:
            raise CommandNotAllowedError("No device is configured.")
        canonical = normalize_interface(interface)
        if state not in _STATES:
            raise CommandNotAllowedError("Unknown interface policy state.")
        if state == "OPERABLE":
            assert_interface_writable(canonical)
        with self._lock:
            data, load_error = self._load_unlocked()
            if load_error is not None:
                raise CommandNotAllowedError(
                    "The local interface policy is invalid. Repair or remove it before editing policy."
                )
            device = data["devices"].setdefault(device_key(host), {"interfaces": {}})
            if state == "UNMANAGED":
                device["interfaces"].pop(canonical, None)
            else:
                device["interfaces"][canonical] = state
            self._write_unlocked(data)

    def ensure_protected(self, host: str | None, interface: str | None) -> None:
        """Persist deterministic protection without ever granting authority."""
        if not host or not interface:
            return
        canonical = normalize_interface(interface)
        with self._lock:
            data, load_error = self._load_unlocked()
            if load_error is not None:
                return
            device = data["devices"].setdefault(device_key(host), {"interfaces": {}})
            if device["interfaces"].get(canonical) == "PROTECTED":
                return
            device["interfaces"][canonical] = "PROTECTED"
            self._write_unlocked(data)

    def annotate(self, host: str | None, interfaces: Iterable[object]) -> None:
        for interface in interfaces:
            port = getattr(interface, "port", "")
            state = self.state_for(host, port)
            setattr(interface, "policy_state", state)
            setattr(interface, "protected", state == "PROTECTED")

    def assert_operable(self, host: str | None, interface: str) -> str:
        canonical = assert_interface_writable(interface)
        if not host:
            raise CommandNotAllowedError("No configured device policy can authorize this interface.")
        with self._lock:
            data, load_error = self._load_unlocked()
            if load_error is not None:
                raise CommandNotAllowedError(
                    "The local interface policy is invalid; all interfaces are read-only."
                )
            state = (
                data["devices"]
                .get(device_key(host), {})
                .get("interfaces", {})
                .get(canonical, "UNMANAGED")
            )
            if state == "PROTECTED":
                raise ProtectedInterfaceError(
                    f"Interface {canonical} is protected and cannot be modified."
                )
            if state != "OPERABLE":
                raise CommandNotAllowedError(
                    f"Interface {canonical} is unmanaged and cannot be modified."
                )
            return canonical

    @contextmanager
    def operation_guard(self, host: str | None, interface: str) -> Iterator[str]:
        """Hold policy stable for the entire device transaction."""
        with self._lock:
            data, load_error = self._load_unlocked()
            if load_error is not None or not data["controlledWritesEnabled"]:
                raise WriteActionsDisabledError(
                    "Controlled writes are disabled in local SwitchOps settings."
                )
            canonical = self.assert_operable(host, interface)
            yield canonical


def _is_device_key(value: str) -> bool:
    return len(value) == 64 and all(char in "0123456789abcdef" for char in value)


_store: InterfacePolicyStore | None = None


def get_interface_policy_store() -> InterfacePolicyStore:
    global _store
    if _store is None:
        _store = InterfacePolicyStore()
    return _store
