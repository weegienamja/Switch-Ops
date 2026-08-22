"""Private trust-on-first-use storage for the managed switch SSH host key."""
from __future__ import annotations

import os
from pathlib import Path
from threading import Lock

import paramiko

from .config import DATA_DIR
from .errors import HostKeyChangedError
from .file_security import harden_private_file


HOST_KEY_FILE = DATA_DIR / "switch_known_hosts"
_LOCK = Lock()


def _load(path: Path) -> paramiko.HostKeys:
    keys = paramiko.HostKeys()
    if path.exists():
        try:
            keys.load(str(path))
        except Exception as exc:
            raise HostKeyChangedError(
                "The local SSH host-key trust file is invalid."
            ) from exc
    return keys


def is_host_pinned(host: str, path: Path = HOST_KEY_FILE) -> bool:
    """Return whether this host already has at least one trusted key."""
    with _LOCK:
        return _load(path).lookup(host) is not None


def configure_paramiko_policy(
    client: paramiko.SSHClient,
    host: str,
    path: Path = HOST_KEY_FILE,
) -> bool:
    """Load a pin and reject changes, or permit only the initial TOFU connection."""
    pinned = is_host_pinned(host, path)
    if pinned:
        client.load_host_keys(str(path))
        client.set_missing_host_key_policy(paramiko.RejectPolicy())
    else:
        client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    return pinned


def verify_and_pin_host_key(
    host: str,
    key: paramiko.PKey,
    path: Path = HOST_KEY_FILE,
) -> bool:
    """Verify an existing key or atomically persist the first observed key.

    Returns ``True`` only when a new pin was written.
    """
    with _LOCK:
        keys = _load(path)
        existing = keys.lookup(host)
        key_type = key.get_name()
        if existing is not None:
            expected = existing.get(key_type)
            if expected is None or expected != key:
                raise HostKeyChangedError(
                    "The switch SSH host key changed; connection refused."
                )
            return False

        keys.add(host, key_type, key)
        path.parent.mkdir(parents=True, exist_ok=True)
        temp_path = path.with_name(f".{path.name}.tmp")
        keys.save(str(temp_path))
        harden_private_file(temp_path)
        os.replace(temp_path, path)
        harden_private_file(path)
        return True
