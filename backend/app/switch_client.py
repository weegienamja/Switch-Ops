"""Switch clients.

- ``NetmikoSwitchClient`` is the preferred real client.
- ``LegacyParamikoSwitchClient`` is the fallback for old IOS SSH suites.
- ``MockSwitchClient`` serves canned outputs from ``sample_outputs/``.

All clients implement ``run(symbol)`` where ``symbol`` is a registry name.
"""
from __future__ import annotations

import logging
import re
from pathlib import Path
from threading import Lock
from typing import Literal, Optional, Protocol

from .command_registry import (
    READ_ONLY_COMMANDS,
    assert_interface_writable,
    resolve_read_command,
    sanitize_description,
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
    "show_lldp_neighbors": "show_lldp_neighbors.txt",
    "show_lldp_neighbors_detail": "show_lldp_neighbors_detail.txt",
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
    def run_raw_action(self, commands: list[str]) -> str: ...
    def close(self) -> None: ...
    def is_alive(self) -> bool: ...
    def refresh_prompt(self) -> None: ...


class MockSwitchClient:
    """Stateful IOS simulator backed by the canned read-only fixtures."""

    _WRITABLE_SHORT = {f"Gi0/{number}" for number in range(3, 9)}

    def __init__(self, sample_dir: Path = SAMPLE_DIR) -> None:
        self.sample_dir = sample_dir
        self._admin_overrides: dict[str, bool] = {}
        self._description_overrides: dict[str, str] = {}
        self._poe_overrides: dict[str, str] = {}
        self._startup_config = self._render_running_config()

    def _fixture(self, symbol: str) -> str:
        filename = _SAMPLE_FILES.get(symbol)
        if not filename:
            return ""
        scenario = get_mock_scenario()
        scenario_path = self.sample_dir / "scenarios" / scenario / filename
        path = scenario_path if scenario != "baseline" and scenario_path.exists() else self.sample_dir / filename
        if not path.exists():
            return ""
        return path.read_text(encoding="utf-8")

    def _render_status(self) -> str:
        from .parsers.interfaces import parse_interface_status

        rows = parse_interface_status(self._fixture("show_interfaces_status"))
        rendered = ["Port      Name               Status       Vlan       Duplex  Speed Type"]
        for row in rows:
            canonical = (
                assert_interface_writable(row.port) if row.port in self._WRITABLE_SHORT else None
            )
            if canonical in self._description_overrides:
                row.name = self._description_overrides[canonical]
            if canonical in self._admin_overrides:
                row.status = (
                    "disabled"
                    if self._admin_overrides[canonical]
                    else ("connected" if row.status == "connected" else "notconnect")
                )
            rendered.append(
                f"{row.port:<10}{row.name:<19}{row.status:<13}{row.vlan:<11}"
                f"{row.duplex:>7} {row.speed:>6} {row.type}"
            )
        return "\n".join(rendered)

    def _render_poe(self) -> str:
        from .parsers.poe import parse_poe

        poe = parse_poe(self._fixture("show_power_inline"))
        rendered = [
            f"Available:{poe.available_watts:.1f}(w)  Used:{poe.used_watts:.1f}(w)  "
            f"Remaining:{poe.remaining_watts:.1f}(w)",
            "",
            "Interface Admin  Oper       Power   Device              Class Max",
            "                            (Watts)",
            "--------- ------ ---------- ------- ------------------- ----- ----",
        ]
        for row in poe.ports:
            canonical = row.interface.replace("Gi", "GigabitEthernet", 1)
            admin = self._poe_overrides.get(canonical, row.admin)
            oper = "off" if admin == "never" else row.oper
            rendered.append(
                f"{row.interface:<9} {admin:<6} {oper:<10} {row.power_watts:<7.1f} "
                f"{row.device:<19} {row.poe_class:<5} {row.max_watts:.1f}"
            )
        return "\n".join(rendered)

    def _render_running_config(self) -> str:
        config = self._fixture("show_running_config")
        changed = set(self._admin_overrides) | set(self._description_overrides) | set(self._poe_overrides)
        for canonical in sorted(changed):
            pattern = re.compile(
                rf"(^interface\s+{re.escape(canonical)}\s*$\n)(?P<body>.*?)(?=^!\s*$)",
                re.MULTILINE | re.DOTALL | re.IGNORECASE,
            )

            def replace_block(match: re.Match[str]) -> str:
                kept: list[str] = []
                for raw in match.group("body").splitlines():
                    line = raw.strip().lower()
                    if canonical in self._admin_overrides and line in {"shutdown", "no shutdown"}:
                        continue
                    if canonical in self._description_overrides and line.startswith("description "):
                        continue
                    if canonical in self._poe_overrides and line.startswith("power inline "):
                        continue
                    kept.append(raw)
                description = self._description_overrides.get(canonical)
                if description:
                    kept.append(f" description {description}")
                if self._admin_overrides.get(canonical) is True:
                    kept.append(" shutdown")
                if self._poe_overrides.get(canonical) == "never":
                    kept.append(" power inline never")
                body = "\n".join(line for line in kept if line)
                return match.group(1) + (body + "\n" if body else "")

            config, count = pattern.subn(replace_block, config, count=1)
            if count != 1:
                raise CommandNotAllowedError(f"Mock interface block missing for {canonical}.")
        return config

    def run(self, symbol: str) -> str:
        if symbol not in READ_ONLY_COMMANDS:
            raise CommandNotAllowedError(f"Unknown read-only command: {symbol!r}")
        if symbol == "terminal_length_0":
            return ""
        if symbol == "show_interfaces_status":
            return self._render_status()
        if symbol == "show_power_inline":
            return self._render_poe()
        if symbol == "show_running_config":
            return self._render_running_config()
        if symbol == "show_startup_config":
            return self._startup_config
        return self._fixture(symbol)

    def run_raw_action(self, commands: list[str]) -> str:
        if commands == ["write memory"]:
            self._startup_config = self._render_running_config()
            return "Building configuration...\n[OK]"
        if len(commands) != 4 or commands[0] != "configure terminal" or commands[-1] != "end":
            return "% Invalid input detected at '^' marker."
        interface_line, action = commands[1], commands[2]
        if not interface_line.startswith("interface "):
            return "% Invalid input detected at '^' marker."
        canonical = assert_interface_writable(interface_line[len("interface ") :])
        if action == "shutdown":
            self._admin_overrides[canonical] = True
        elif action == "no shutdown":
            self._admin_overrides[canonical] = False
        elif action in {"power inline auto", "power inline never"}:
            self._poe_overrides[canonical] = action.rsplit(" ", 1)[-1]
        elif action == "no description":
            self._description_overrides[canonical] = ""
        elif action.startswith("description "):
            self._description_overrides[canonical] = sanitize_description(
                action[len("description ") :]
            )
        else:
            return "% Invalid input detected at '^' marker."
        return "Mock IOS accepted the bounded configuration operation."

    def run_command_sequence(self, commands: list[str]) -> str:
        return self.run_raw_action(commands)

    def is_alive(self) -> bool:
        return True

    def refresh_prompt(self) -> None:
        return None

    def close(self) -> None:  # pragma: no cover
        pass


class NetmikoSwitchClient:
    """Netmiko-backed client with legacy SSH options."""

    def __init__(self, creds: SwitchCredentials, *, legacy_algorithms: bool = True) -> None:
        self.creds = creds
        self.legacy_algorithms = legacy_algorithms
        self._conn = None
        # Netmiko's default read loop waits out a fixed settling delay before
        # returning, which costs ~500 ms per command on this device regardless
        # of how little output there is. Anchoring the read to the device
        # prompt returns as soon as the prompt appears. Measured on the lab
        # Catalyst: 507 ms -> 46 ms for `show interfaces status`, byte-identical
        # output across all thirteen dashboard commands.
        self._prompt_pattern: str | None = None

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
            self.refresh_prompt()
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

    def refresh_prompt(self) -> None:
        """Capture the device prompt so reads can be anchored to it.

        Called after connect and after anything that could change the prompt,
        such as leaving configuration mode. A failure here is not fatal: the
        client simply falls back to Netmiko's slower pattern search.
        """
        if self._conn is None:
            return
        try:
            prompt = self._conn.find_prompt()
        except Exception:  # pragma: no cover - transport dependent
            self._prompt_pattern = None
            return
        prompt = (prompt or "").strip()
        # Only trust a prompt that looks like a privileged EXEC prompt. A
        # config-mode prompt would anchor reads to the wrong string.
        self._prompt_pattern = re.escape(prompt) if prompt.endswith("#") else None

    def run(self, symbol: str) -> str:
        if self._conn is None:
            self.connect()
        assert self._conn is not None
        command = resolve_read_command(symbol)
        if symbol == "terminal_length_0":
            self._conn.send_command_timing(command)
            return ""
        if self._prompt_pattern:
            try:
                return self._conn.send_command(
                    command, expect_string=self._prompt_pattern, read_timeout=30
                )
            except Exception:
                # The prompt may have moved underneath us. Drop the anchor and
                # let the next call re-establish it rather than failing a read.
                logger.warning("Prompt-anchored read failed for %s; using pattern search.", symbol)
                self._prompt_pattern = None
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
        # Configuration mode changes the prompt; re-anchor before the next read.
        self.refresh_prompt()
        return "\n".join(o for o in outputs if o)

    def is_alive(self) -> bool:
        """Whether the transport still looks usable, without sending anything."""
        if self._conn is None:
            return False
        try:
            return bool(self._conn.is_alive())
        except Exception:  # pragma: no cover - transport dependent
            return False

    def close(self) -> None:
        if self._conn is not None:
            try:
                self._conn.disconnect()
            except Exception:  # pragma: no cover
                pass
            self._conn = None
            self._prompt_pattern = None


class LegacyParamikoSwitchClient:
    def __init__(self, creds: SwitchCredentials) -> None:
        self.creds = creds
        self._client: Optional[LegacyParamikoClient] = None

    def is_alive(self) -> bool:
        return self._client is not None

    def refresh_prompt(self) -> None:
        return None

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


def connect_switch_client(creds: SwitchCredentials) -> SwitchClient:
    """Open a bounded IOS/IOS-XE session for an explicitly supplied target.

    Lab Assurance uses this for secondary devices. The caller owns and must
    close the returned client. It is intentionally not exposed through an API
    that accepts commands: callers can execute only command-registry symbols.
    """
    settings = get_settings()
    register_secret(creds.switch_password)
    register_secret(creds.switch_enable_secret)
    try:
        client = NetmikoSwitchClient(creds, legacy_algorithms=settings.legacy_ssh)
        client.connect()
        return client
    except LegacySshNegotiationError:
        if not settings.legacy_ssh:
            raise
        logger.warning("Falling back to legacy Paramiko for a Lab Assurance device.")
        legacy = LegacyParamikoSwitchClient(creds)
        legacy.connect()
        return legacy
