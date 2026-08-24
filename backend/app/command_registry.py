"""Command and action allowlists.

This module is the *only* place that maps symbolic names to literal IOS
commands. There is no path from HTTP input to a Cisco device that bypasses
these tables.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Dict, List

from .errors import (
    CommandNotAllowedError,
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
    "show_lldp_neighbors": "show lldp neighbors",
    "show_lldp_neighbors_detail": "show lldp neighbors detail",
    # ARP maps the configured default gateway to a hardware address, which the
    # MAC table then maps to a physical port. That chain is the only evidence
    # this switch has about *which way* the gateway lies.
    "show_ip_arp": "show ip arp",
    "show_interfaces_trunk": "show interfaces trunk",
    "show_environment": "show environment",
    "show_environment_all": "show environment all",
    # Lab Assurance routing/control-plane observation. Each entry is a fixed
    # read-only command; unsupported output becomes capability evidence and is
    # never treated as an empty healthy result.
    "show_ip_route": "show ip route",
    "show_ip_protocols": "show ip protocols",
    "show_ip_ospf_neighbor": "show ip ospf neighbor",
    "show_ip_eigrp_neighbors": "show ip eigrp neighbors",
    "show_bgp_ipv4_unicast_summary": "show bgp ipv4 unicast summary",
    "show_standby_brief": "show standby brief",
    "show_vrf": "show vrf",
    "show_bfd_neighbors": "show bfd neighbors",
    "show_ip_sla_summary": "show ip sla summary",
    "show_nve_peers": "show nve peers",
    "show_bgp_l2vpn_evpn_summary": "show bgp l2vpn evpn summary",
    "show_segment_routing_mpls_sid_map": "show segment-routing mpls connected-prefix-sid-map",
    "show_segment_routing_srv6_locator": "show segment-routing srv6 locator",
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
    ],
    "disable_port": [
        "configure terminal",
        "interface {iface}",
        "shutdown",
        "end",
    ],
    "set_port_description": [
        "configure terminal",
        "interface {iface}",
        "description {value}",
        "end",
    ],
    "enable_poe": [
        "configure terminal",
        "interface {iface}",
        "power inline auto",
        "end",
    ],
    "save_config": ["write memory"],
    "backup_config": ["terminal length 0", "show running-config"],
}


_PHYSICAL_PREFIXES = {
    "fa": "FastEthernet",
    "fastethernet": "FastEthernet",
    "gi": "GigabitEthernet",
    "gigabitethernet": "GigabitEthernet",
    "te": "TenGigabitEthernet",
    "tengigabitethernet": "TenGigabitEthernet",
    "tw": "TwentyFiveGigE",
    "twe": "TwentyFiveGigE",
    "twentyfivegige": "TwentyFiveGigE",
    "fo": "FortyGigabitEthernet",
    "fortygigabitethernet": "FortyGigabitEthernet",
    "hu": "HundredGigE",
    "hundredgige": "HundredGigE",
}

_SHORT_PREFIXES = {
    "FastEthernet": "Fa",
    "GigabitEthernet": "Gi",
    "TenGigabitEthernet": "Te",
    "TwentyFiveGigE": "Twe",
    "FortyGigabitEthernet": "Fo",
    "HundredGigE": "Hu",
}


_DESCRIPTION_OK = set(
    "abcdefghijklmnopqrstuvwxyz"
    "ABCDEFGHIJKLMNOPQRSTUVWXYZ"
    "0123456789 -_./"
)


def normalize_interface(name: str) -> str:
    """Return a canonical, injection-safe Cisco interface name.

    Physical interfaces may use two- or three-level numbering, such as
    ``Gi0/6``, ``Gi1/0/48`` or ``Te1/1/1``. VLAN interfaces are accepted for
    observation and protection policy, but are never physical write targets.
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
    prefix = next(
        (candidate for candidate in sorted(_PHYSICAL_PREFIXES, key=len, reverse=True)
         if lower.startswith(candidate)),
        None,
    )
    if prefix is None:
        raise CommandNotAllowedError(f"Unsupported interface: {name!r}")
    suffix = stripped[len(prefix):]
    if not re.fullmatch(r"\d+(?:/\d+){1,2}", suffix):
        raise CommandNotAllowedError(f"Malformed interface suffix: {name!r}")
    normalized_suffix = "/".join(str(int(part)) for part in suffix.split("/"))
    return f"{_PHYSICAL_PREFIXES[prefix]}{normalized_suffix}"


def is_physical_interface(name: str) -> bool:
    try:
        canonical = normalize_interface(name)
    except CommandNotAllowedError:
        return False
    return not canonical.startswith("Vlan")


def short_interface(name: str) -> str:
    canonical = normalize_interface(name)
    for long_prefix, short_prefix in _SHORT_PREFIXES.items():
        if canonical.startswith(long_prefix):
            return canonical.replace(long_prefix, short_prefix, 1)
    return canonical


def assert_interface_writable(name: str) -> str:
    """Validate only that a name is a supported physical write target.

    Device-specific permission is enforced by ``interface_policy`` immediately
    before a transaction. Keeping syntax validation here prevents raw input
    from ever becoming an IOS command while avoiding any global port layout.
    """
    canonical = normalize_interface(name)
    if not is_physical_interface(canonical):
        raise CommandNotAllowedError(
            f"Interface {canonical} is not a supported physical write target."
        )
    return canonical


def assert_interface_readable(name: str) -> str:
    """Validate a guide/filter interface without granting write authority."""
    return normalize_interface(name)


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
