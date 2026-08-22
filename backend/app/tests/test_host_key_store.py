"""SSH host-key trust-on-first-use tests."""
from __future__ import annotations

import paramiko
import pytest

from app.errors import HostKeyChangedError
from app.host_key_store import (
    configure_paramiko_policy,
    is_host_pinned,
    verify_and_pin_host_key,
)


def test_first_key_is_pinned_and_reused(tmp_path):
    path = tmp_path / "known_hosts"
    key = paramiko.RSAKey.generate(1024)

    assert is_host_pinned("switch.test", path) is False
    assert verify_and_pin_host_key("switch.test", key, path) is True
    assert is_host_pinned("switch.test", path) is True
    assert verify_and_pin_host_key("switch.test", key, path) is False

    client = paramiko.SSHClient()
    assert configure_paramiko_policy(client, "switch.test", path) is True


def test_changed_key_is_rejected(tmp_path):
    path = tmp_path / "known_hosts"
    original = paramiko.RSAKey.generate(1024)
    changed = paramiko.RSAKey.generate(1024)
    verify_and_pin_host_key("switch.test", original, path)

    with pytest.raises(HostKeyChangedError):
        verify_and_pin_host_key("switch.test", changed, path)
