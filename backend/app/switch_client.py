"""Switch clients.

- ``NetmikoSwitchClient`` is the preferred real client.
- ``LegacyParamikoSwitchClient`` is the fallback for old IOS SSH suites.
- ``MockSwitchClient`` serves canned outputs from ``sample_outputs/``.

All clients implement ``run(symbol)`` where ``symbol`` is a registry name.
"""
from __future__ import annotations

import logging
from pathlib import Path
from threading import Lock
from typing import Literal, Optional, Protocol

from .command_registry import (
    READ_ONLY_COMMANDS,
    resolve_read_command,
)
from .config import get_settings, SAMPLE_DIR
from .credential_store import SwitchCredentials, get_credential_store
from .errors import (
    CommandNotAllowedError,
    CredentialsMissingError,
    HostKeyChangedError,
    LegacySshNegotiationError,
    SwitchConnectionError,
)
from .host_key_store import HOST_KEY_FILE, is_host_pinned, verify_and_pin_host_key
from .logging_config import register_secret
from .legacy_ssh_client import (
    LEGACY_CIPHERS,
    LEGACY_HOSTKEYS,
    LEGACY_KEX,
    LEGACY_MACS,
    LEGACY_PUBKEYS,
    LegacyParamikoClient,
    _patch_paramiko_preferences,
)

logger = logging.getLogger(__name__)


_SAMPLE_FILES = {
    "show_version": "show_version.txt",
    "show_inventory": "show_inventory.txt",
    "show_running_config": "show_running_config.txt",
    "show_startup_config": "show_startup_config.txt",
    "show_ip_interface_brief": "show_ip_interface_brief.txt",
    "show_interfaces_status": "show_interfaces_status.txt",
    "show_interfaces_counters_errors": "show_interfaces_counters_errors.txt",
    "show_power_inline": "show_power_inline.txt",
    "show_env_all": "show_env_all.txt",
    "show_processes_cpu": "show_processes_cpu.txt",
    "show_memory_statistics": "show_memory_statistics.txt",
    "show_mac_address_table": "show_mac_address_table.txt",
    "show_logging": "show_logging.txt",
    "show_vlan_brief": "show_vlan_brief.txt",
    "show_spanning_tree": "show_spanning_tree.txt",
    "show_cdp_neighbors_detail": "show_cdp_neighbors_detail.txt",
}

MockScenario = Literal["baseline", "ap_attached"]
_mock_scenario: MockScenario = "baseline"
_mock_scenario_lock = Lock()


def get_mock_scenario() -> MockScenario:
    with _mock_scenario_lock:
        return _mock_scenario


def set_mock_scenario(scenario: MockScenario) -> MockScenario:
    if scenario not in {"baseline", "ap_attached"}:
        raise CommandNotAllowedError(f"Unknown mock scenario: {scenario!r}")
    global _mock_scenario
    with _mock_scenario_lock:
        _mock_scenario = scenario
    return scenario


class SwitchClient(Protocol):
    def run(self, symbol: str) -> str: ...
    def close(self) -> None: ...


class MockSwitchClient:
    """Returns canned IOS output for the configured set of read commands."""

    def __init__(self, sample_dir: Path = SAMPLE_DIR) -> None:
        self.sample_dir = sample_dir

    def run(self, symbol: str) -> str:
        if symbol not in READ_ONLY_COMMANDS:
            raise CommandNotAllowedError(f"Unknown read-only command: {symbol!r}")
        if symbol == "terminal_length_0":
            return ""
        filename = _SAMPLE_FILES.get(symbol)
        if not filename:
            return ""
        scenario = get_mock_scenario()
        scenario_path = self.sample_dir / "scenarios" / scenario / filename
        path = scenario_path if scenario != "baseline" and scenario_path.exists() else self.sample_dir / filename
        if not path.exists():
            return ""
        return path.read_text(encoding="utf-8")

    def run_command_sequence(self, commands: list[str]) -> str:
        return "Mock mode — write actions are simulated.\n" + "\n".join(commands)

    def close(self) -> None:  # pragma: no cover
        pass


class NetmikoSwitchClient:
    """Netmiko-backed client with legacy SSH options."""

    def __init__(self, creds: SwitchCredentials, *, legacy_algorithms: bool = True) -> None:
        self.creds = creds
        self.legacy_algorithms = legacy_algorithms
        self._conn = None

    def connect(self) -> None:
        try:
            from netmiko import ConnectHandler  # type: ignore
        except ImportError as exc:  # pragma: no cover
            raise SwitchConnectionError("netmiko not installed") from exc

        if self.legacy_algorithms:
            _patch_paramiko_preferences()

        host_key_pinned = is_host_pinned(self.creds.switch_host)
        params = {
            "device_type": self.creds.switch_device_type or "cisco_ios",
            "host": self.creds.switch_host,
            "username": self.creds.switch_username,
            "password": self.creds.switch_password,
            "secret": self.creds.switch_enable_secret or self.creds.switch_password,
            "fast_cli": False,
            "conn_timeout": 20,
            "auth_timeout": 20,
            "banner_timeout": 20,
            "session_log": None,
            "disabled_algorithms": {},
            "ssh_strict": host_key_pinned,
            "alt_host_keys": host_key_pinned,
            "alt_key_file": str(HOST_KEY_FILE) if host_key_pinned else "",
        }
        try:
            self._conn = ConnectHandler(**params)
            ssh_client = getattr(self._conn, "remote_conn_pre", None)
            transport = ssh_client.get_transport() if ssh_client is not None else None
            if transport is None:
                raise SwitchConnectionError("SSH transport was not established.")
            verify_and_pin_host_key(
                self.creds.switch_host,
                transport.get_remote_server_key(),
            )
            if not self._conn.check_enable_mode():
                self._conn.enable()
            self._conn.send_command_timing("terminal length 0")
        except HostKeyChangedError:
            self.close()
            raise
        except Exception as exc:
            cls = type(exc).__name__
            if "Kex" in cls or "Negotiation" in cls or "SSH" in cls:
                raise LegacySshNegotiationError(
                    "Netmiko SSH negotiation failed", detail=str(exc)
                ) from exc
            raise SwitchConnectionError("Netmiko connection failed", detail=str(exc)) from exc

    def run(self, symbol: str) -> str:
        if self._conn is None:
            self.connect()
        assert self._conn is not None
        command = resolve_read_command(symbol)
        if symbol == "terminal_length_0":
            self._conn.send_command_timing(command)
            return ""
        return self._conn.send_command(command, read_timeout=30)

    def run_raw_action(self, commands: list[str]) -> str:
        """Run a pre-validated write-action sequence.

        ``commands`` must come from ``command_registry.build_write_action``.
        """
        if self._conn is None:
            self.connect()
        assert self._conn is not None
        # Use config_mode for the configure terminal block, send other lines directly.
        outputs: list[str] = []
        in_config = False
        config_buffer: list[str] = []
        for cmd in commands:
            if cmd == "configure terminal":
                in_config = True
                continue
            if cmd == "end":
                if in_config and config_buffer:
                    outputs.append(self._conn.send_config_set(config_buffer))
                in_config = False
                config_buffer = []
                continue
            if in_config:
                config_buffer.append(cmd)
                continue
            outputs.append(self._conn.send_command_timing(cmd))
        if in_config and config_buffer:
            outputs.append(self._conn.send_config_set(config_buffer))
        return "\n".join(o for o in outputs if o)

    def close(self) -> None:
        if self._conn is not None:
            try:
                self._conn.disconnect()
            except Exception:  # pragma: no cover
                pass
            self._conn = None


class LegacyParamikoSwitchClient:
    def __init__(self, creds: SwitchCredentials) -> None:
        self.creds = creds
        self._client: Optional[LegacyParamikoClient] = None

    def connect(self) -> None:
        client = LegacyParamikoClient(self.creds)
        client.connect()
        if self.creds.switch_enable_secret:
            client.enable(self.creds.switch_enable_secret)
        self._client = client

    def run(self, symbol: str) -> str:
        if self._client is None:
            self.connect()
        assert self._client is not None
        command = resolve_read_command(symbol)
        return self._client.run(command)

    def run_raw_action(self, commands: list[str]) -> str:
        if self._client is None:
            self.connect()
        assert self._client is not None
        out: list[str] = []
        for cmd in commands:
            out.append(self._client.run(cmd))
        return "\n".join(o for o in out if o)

    def close(self) -> None:
        if self._client is not None:
            self._client.close()
            self._client = None


def get_switch_client() -> SwitchClient:
    settings = get_settings()
    if settings.mock_mode:
        logger.info("Using MockSwitchClient (SWITCH_MOCK_MODE=true).")
        return MockSwitchClient()
    creds = get_credential_store().load()
    if creds is None:
        raise CredentialsMissingError(
            "No switch credentials configured. Run the setup wizard."
        )
    register_secret(creds.switch_password)
    register_secret(creds.switch_enable_secret)
    # Try Netmiko first, fall back to legacy Paramiko on negotiation failure.
    try:
        client = NetmikoSwitchClient(creds, legacy_algorithms=settings.legacy_ssh)
        client.connect()
        return client
    except LegacySshNegotiationError:
        if not settings.legacy_ssh:
            raise
        logger.warning("Falling back to legacy Paramiko transport.")
        legacy = LegacyParamikoSwitchClient(creds)
        legacy.connect()
        return legacy
