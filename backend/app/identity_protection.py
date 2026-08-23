"""Local pseudonymization for cross-provider identifiers."""
from __future__ import annotations

import hashlib
import hmac
import ipaddress
import os
from pathlib import Path
import re

from .config import DATA_DIR
from .file_security import harden_private_file


IDENTITY_KEY_FILE = DATA_DIR / "unified-identity.key"


def normalize_serial(value: str) -> str:
    return re.sub(r"\s+", "", value).upper()


def normalize_hardware_mac(value: str) -> str:
    compact = re.sub(r"[-:.]", "", value.strip().lower())
    return compact if re.fullmatch(r"[0-9a-f]{12}", compact) else ""


def is_globally_administered_device_mac(value: str) -> bool:
    normalized = normalize_hardware_mac(value)
    if not normalized or normalized in {"0" * 12, "f" * 12}:
        return False
    first = int(normalized[:2], 16)
    return not bool(first & 0x01) and not bool(first & 0x02)


def normalize_management_address(value: str) -> str:
    try:
        return str(ipaddress.ip_address(value.strip()))
    except ValueError:
        return ""


class IdentityProtector:
    """HMAC private identifiers with a host-local secret.

    Tests and import tools can inject a fixed key. Runtime callers use the
    private per-install key generated below.
    """

    def __init__(self, key: bytes | None = None, key_path: Path = IDENTITY_KEY_FILE) -> None:
        self._key = key or self._load_or_create_key(key_path)

    @staticmethod
    def _load_or_create_key(path: Path) -> bytes:
        path.parent.mkdir(parents=True, exist_ok=True)
        if path.exists():
            key = path.read_bytes()
            if len(key) >= 32:
                return key
            raise RuntimeError("Unified identity key is invalid.")
        key = os.urandom(32)
        temporary = path.with_suffix(path.suffix + ".tmp")
        temporary.write_bytes(key)
        harden_private_file(temporary)
        try:
            temporary.replace(path)
            harden_private_file(path)
        finally:
            if temporary.exists():
                temporary.unlink()
        return key

    def protect(self, kind: str, value: str) -> str:
        normalized = value.strip().lower()
        digest = hmac.new(
            self._key,
            f"{kind}|{normalized}".encode("utf-8"),
            hashlib.sha256,
        ).hexdigest()[:24]
        safe_kind = re.sub(r"[^a-z0-9]+", "-", kind.lower()).strip("-") or "value"
        return f"pid-{safe_kind}-{digest}"

    def serial(self, value: str) -> str | None:
        normalized = normalize_serial(value)
        return self.protect("serial", normalized) if normalized else None

    def hardware_mac(self, value: str, *, kind: str = "device-mac") -> str | None:
        normalized = normalize_hardware_mac(value)
        if not is_globally_administered_device_mac(normalized):
            return None
        # Device inventory and LLDP use different labels for the same hardware
        # identifier. They intentionally share one HMAC namespace.
        return self.protect("hardware-mac", normalized)

    def management_address(self, value: str) -> str | None:
        normalized = normalize_management_address(value)
        return self.protect("management-address", normalized) if normalized else None
