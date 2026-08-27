"""Legacy SSH transport for old Cisco IOS (12.2(55)EX2).

We patch a Paramiko ``Transport`` to advertise the deprecated KEX/cipher/MAC
algorithms required by the WS-C3560CG-8PC-S, then drive an interactive shell
similar to Netmiko's ``send_command``.
"""
from __future__ import annotations

import logging
import time
from typing import List, Optional

from .errors import (
    SwitchConnectionError,
    classify_connection_exception,
    is_device_transport_exception,
    session_lost_error,
)
from .credential_store import SwitchCredentials
from .host_key_store import configure_paramiko_policy, verify_and_pin_host_key

logger = logging.getLogger(__name__)


LEGACY_KEX = ["diffie-hellman-group1-sha1"]
LEGACY_CIPHERS = ["aes128-cbc", "3des-cbc", "aes256-cbc"]
LEGACY_MACS = ["hmac-sha1", "hmac-sha1-96", "hmac-md5", "hmac-md5-96"]
LEGACY_HOSTKEYS = ["ssh-rsa"]
LEGACY_PUBKEYS = ["ssh-rsa"]


def _patch_paramiko_preferences() -> None:
    """Allow required legacy algorithms inside this process, idempotently."""
    from paramiko import Transport

    def prepend_unique(legacy: list[str], current: tuple[str, ...]) -> tuple[str, ...]:
        return tuple(legacy) + tuple(item for item in current if item not in legacy)

    Transport._preferred_kex = prepend_unique(LEGACY_KEX, tuple(Transport._preferred_kex))  # type: ignore[attr-defined]
    Transport._preferred_ciphers = prepend_unique(LEGACY_CIPHERS, tuple(Transport._preferred_ciphers))  # type: ignore[attr-defined]
    Transport._preferred_macs = prepend_unique(LEGACY_MACS, tuple(Transport._preferred_macs))  # type: ignore[attr-defined]
    Transport._preferred_keys = prepend_unique(LEGACY_HOSTKEYS, tuple(Transport._preferred_keys))  # type: ignore[attr-defined]
    try:
        Transport._preferred_pubkeys = prepend_unique(  # type: ignore[attr-defined]
            LEGACY_PUBKEYS, tuple(getattr(Transport, "_preferred_pubkeys", ()))
        )
    except Exception:
        pass


class LegacyParamikoClient:
    """Minimal Paramiko shell-based runner for old IOS devices."""

    PROMPT_CHARS = ("#", ">")

    def __init__(self, creds: SwitchCredentials, timeout: float = 20.0) -> None:
        self.creds = creds
        self.timeout = timeout
        self._client = None
        self._shell = None

    def __enter__(self) -> "LegacyParamikoClient":
        self.connect()
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        self.close()

    def connect(self) -> None:
        try:
            import paramiko
        except ImportError as exc:  # pragma: no cover
            raise SwitchConnectionError("paramiko is not installed") from exc

        _patch_paramiko_preferences()

        client = paramiko.SSHClient()
        configure_paramiko_policy(client, self.creds.switch_host)
        try:
            client.connect(
                hostname=self.creds.switch_host,
                username=self.creds.switch_username,
                password=self.creds.switch_password,
                timeout=self.timeout,
                allow_agent=False,
                look_for_keys=False,
                disabled_algorithms={},
            )
            transport = client.get_transport()
            if transport is None:
                raise SwitchConnectionError("SSH transport was not established.")
            verify_and_pin_host_key(
                self.creds.switch_host,
                transport.get_remote_server_key(),
            )
            self._client = client
            self._shell = client.invoke_shell(width=200, height=2000)
            self._shell.settimeout(self.timeout)
            self._read_until_prompt()
            self.send_line("terminal length 0")
        except Exception as exc:
            try:
                client.close()
            finally:
                self._client = None
                self._shell = None
            raise classify_connection_exception(exc) from exc

    def close(self) -> None:
        try:
            if self._shell is not None:
                self._shell.close()
        finally:
            self._shell = None
            if self._client is not None:
                self._client.close()
            self._client = None

    def is_alive(self) -> bool:
        if self._client is None or self._shell is None:
            return False
        try:
            transport = self._client.get_transport()
            return bool(
                transport is not None
                and transport.is_active()
                and not self._shell.closed
            )
        except Exception:
            return False

    def _read_until_prompt(self, extra_terminators: Optional[List[str]] = None) -> str:
        if self._shell is None:
            raise SwitchConnectionError("Shell not initialised.")
        buf = ""
        deadline = time.monotonic() + self.timeout
        terminators = list(extra_terminators or [])
        while time.monotonic() < deadline:
            if self._shell.recv_ready():
                chunk = self._shell.recv(65535).decode("utf-8", errors="replace")
                buf += chunk
                last = buf.rstrip().split("\n")[-1]
                if last.endswith("#") or last.endswith(">"):
                    return buf
                if any(t in buf for t in terminators):
                    return buf
            else:
                time.sleep(0.05)
        return buf

    def send_line(self, line: str) -> str:
        if self._shell is None:
            raise SwitchConnectionError("Shell not initialised.")
        try:
            self._shell.send(line + "\n")
            return self._read_until_prompt()
        except Exception as exc:
            if is_device_transport_exception(exc):
                self.close()
                raise session_lost_error(exc) from exc
            raise

    def run(self, command: str) -> str:
        out = self.send_line(command)
        # Strip the echoed command and trailing prompt
        lines = out.splitlines()
        cleaned: list[str] = []
        for ln in lines:
            stripped = ln.rstrip()
            if stripped.endswith("#") or stripped.endswith(">"):
                # often the trailing prompt — drop only if it's a bare prompt
                if stripped.split()[-1].endswith(("#", ">")) and len(stripped.split()) <= 1:
                    continue
            cleaned.append(ln)
        # Drop the first echoed command if present.
        if cleaned and command in cleaned[0]:
            cleaned = cleaned[1:]
        return "\n".join(cleaned).strip("\r\n")

    def enable(self, enable_secret: str) -> None:
        """Enter enable mode if currently in user EXEC."""
        if self._shell is None:
            raise SwitchConnectionError("Shell not initialised.")
        try:
            # Probe current prompt
            self._shell.send("\n")
            out = self._read_until_prompt()
            last = out.rstrip().split("\n")[-1] if out else ""
            if last.endswith(">"):
                self._shell.send("enable\n")
                self._read_until_prompt(extra_terminators=["Password:"])
                self._shell.send(enable_secret + "\n")
                self._read_until_prompt()
        except Exception as exc:
            if is_device_transport_exception(exc):
                self.close()
                raise session_lost_error(exc) from exc
            raise
