"""Command and action allowlists.

This module is the *only* place that maps symbolic names to literal IOS
commands. There is no path from HTTP input to a Cisco device that bypasses
these tables.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Dict, List, Tuple

from .errors import (
    CommandNotAllowedError,
    ProtectedInterfaceError,
)


# Read-only command set. Symbolic name -> literal IOS command.
READ_ONLY_COMMANDS: Dict[str, str] = {
    "terminal_length_0": "terminal length 0",
    "show_version": "show version",
    "show_inventory": "show inventory",
    "show_running_config": "show running-config",
    "show_startup_config": "show startup-config",
    "show_ip_interface_brief": "show ip interface brief",
    "show_interfaces_status": "show interfaces status",
    "show_interfaces_counters_errors": "show interfaces counters errors",
    "show_power_inline": "show power inline",
    "show_env_all": "show env all",
    "show_processes_cpu": "show processes cpu",
    "show_memory_statistics": "show memory statistics",
    "show_mac_address_table": "show mac address-table",
    "show_logging": "show logging",
    # Additional read-only diagnostics used by the real-device validation
    # path. They remain symbolic and cannot be supplied by an API caller.
    "show_interfaces": "show interfaces",
    "show_processes_cpu_history": "show processes cpu history",
    "show_memory": "show memory",
    "show_interfaces_switchport": "show interfaces switchport",
    "show_vlan_brief": "show vlan brief",
    "show_spanning_tree": "show spanning-tree",
    "show_etherchannel_summary": "show etherchannel summary",
    "show_cdp_neighbors_detail": "show cdp neighbors detail",
    # ARP maps the configured default gateway to a hardware address, which the
    # MAC table then maps to a physical port. That chain is the only evidence
    # this switch has about *which way* the gateway lies.
    "show_ip_arp": "show ip arp",
    "show_interfaces_trunk": "show interfaces trunk",
    "show_environment": "show environment",
    "show_environment_all": "show environment all",
    # NB: do NOT use "show running-config | section line vty" — that fails on
    # IOS 12.2(55)EX2. Use "begin line vty" if ever needed.
    "show_running_config_begin_vty": "show running-config | begin line vty",
}


# Named safe-write actions. Each value is the literal config sequence.
# Use ``{iface}`` placeholder for the (validated) canonical interface name and
# ``{value}`` for a sanitized scalar.
SAFE_WRITE_ACTIONS: Dict[str, List[str]] = {
    "enable_port": [
        "configure terminal",
        "interface {iface}",
        "no shutdown",
        "end",
        "write memory",
    ],
    "disable_port": [
        "configure terminal",
        "interface {iface}",
        "shutdown",
        "end",
        "write memory",
    ],
    "set_port_description": [
        "configure terminal",
        "interface {iface}",
        "description {value}",
        "end",
        "write memory",
    ],
    "enable_poe": [
        "configure terminal",
        "interface {iface}",
        "power inline auto",
        "end",
        "write memory",
    ],
    "save_config": ["write memory"],
    "backup_config": ["terminal length 0", "show running-config"],
}


# Interfaces that may be touched by safe-write actions.
ALLOWLISTED_INTERFACES: Tuple[str, ...] = (
    "GigabitEthernet0/3",
    "GigabitEthernet0/4",
    "GigabitEthernet0/5",
    "GigabitEthernet0/6",
    "GigabitEthernet0/7",
    "GigabitEthernet0/8",
)


# Interfaces that may *never* be touched.
PROTECTED_INTERFACES: Tuple[str, ...] = (
    "GigabitEthernet0/1",
    "GigabitEthernet0/2",
    "Vlan1",
)

# Interfaces that a bounded read-only guide may select for filtering parsed
# results. The value is never interpolated into an IOS command.
READABLE_INTERFACES: Tuple[str, ...] = tuple(
    f"GigabitEthernet0/{number}" for number in range(1, 11)
)


_DESCRIPTION_OK = set(
    "abcdefghijklmnopqrstuvwxyz"
    "ABCDEFGHIJKLMNOPQRSTUVWXYZ"
    "0123456789 -_./"
)


def normalize_interface(name: str) -> str:
    """Normalize ``Gi0/6``, ``gi0/6``, ``GigabitEthernet0/6`` to a canonical
    long form. Vlan interfaces normalize to ``Vlan<n>``.
    """
    if not name:
        raise CommandNotAllowedError("Empty interface name.")
    if any(ord(char) < 32 or ord(char) == 127 for char in name):
        raise CommandNotAllowedError("Interface name contains control characters.")
    stripped = name.strip()
    lower = stripped.lower()
    if lower.startswith("vlan"):
        suffix = lower[4:]
        if not suffix.isdigit():
            raise CommandNotAllowedError(f"Invalid Vlan interface: {name!r}")
        return f"Vlan{int(suffix)}"
    if lower.startswith("gigabitethernet"):
        suffix = stripped[len("GigabitEthernet"):]
    elif lower.startswith("gi"):
        suffix = stripped[2:]
    else:
        raise CommandNotAllowedError(f"Unsupported interface: {name!r}")
    if not re.fullmatch(r"\d+/\d+", suffix):
        raise CommandNotAllowedError(f"Malformed interface suffix: {name!r}")
    return f"GigabitEthernet{suffix}"


def assert_interface_writable(name: str) -> str:
    """Return the canonical name if the interface is writable; raise otherwise."""
    canonical = normalize_interface(name)
    if canonical in PROTECTED_INTERFACES:
        raise ProtectedInterfaceError(
            f"Interface {canonical} is protected and cannot be modified."
        )
    if canonical not in ALLOWLISTED_INTERFACES:
        raise CommandNotAllowedError(
            f"Interface {canonical} is not in the safe-write allowlist."
        )
    return canonical


def assert_interface_readable(name: str) -> str:
    """Validate a guide/filter interface without granting write authority."""
    canonical = normalize_interface(name)
    if canonical not in READABLE_INTERFACES:
        raise CommandNotAllowedError(
            f"Interface {canonical} is not in the read-only interface set."
        )
    return canonical


def resolve_read_command(symbol: str) -> str:
    if symbol not in READ_ONLY_COMMANDS:
        raise CommandNotAllowedError(f"Unknown read-only command: {symbol!r}")
    return READ_ONLY_COMMANDS[symbol]


def sanitize_description(value: str) -> str:
    """Sanitize a port description. Length-bounded and character-bounded."""
    if not value:
        raise CommandNotAllowedError("Empty description.")
    value = value.strip()
    if len(value) > 64:
        raise CommandNotAllowedError("Description must be 64 characters or fewer.")
    bad = [c for c in value if c not in _DESCRIPTION_OK]
    if bad:
        raise CommandNotAllowedError(
            f"Description contains disallowed characters: {''.join(sorted(set(bad)))!r}"
        )
    return value


@dataclass(frozen=True)
class WriteAction:
    name: str
    commands: List[str]
    interface: str | None = None


def build_write_action(
    action: str,
    interface: str | None = None,
    value: str | None = None,
) -> WriteAction:
    """Build a fully-resolved, validated write action."""
    if action not in SAFE_WRITE_ACTIONS:
        raise CommandNotAllowedError(f"Unknown write action: {action!r}")

    template = SAFE_WRITE_ACTIONS[action]

    canonical_iface: str | None = None
    if any("{iface}" in cmd for cmd in template):
        if interface is None:
            raise CommandNotAllowedError(f"Action {action} requires an interface.")
        canonical_iface = assert_interface_writable(interface)

    safe_value: str | None = None
    if any("{value}" in cmd for cmd in template):
        if value is None:
            raise CommandNotAllowedError(f"Action {action} requires a value.")
        safe_value = sanitize_description(value)

    resolved: List[str] = []
    for cmd in template:
        out = cmd
        if "{iface}" in out:
            assert canonical_iface is not None
            out = out.replace("{iface}", canonical_iface)
        if "{value}" in out:
            assert safe_value is not None
            out = out.replace("{value}", safe_value)
        resolved.append(out)

    return WriteAction(name=action, commands=resolved, interface=canonical_iface)
