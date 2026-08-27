"""Safe capability discovery and progressive local-host identification.

No function in this module returns a local MAC address or an SNMP credential.
Hardware addresses are used only in memory to correlate two observations.
"""
from __future__ import annotations

from dataclasses import dataclass
import ipaddress
import re
import socket
from typing import Iterable, Sequence

from .discovery_evidence import normalize_mac, stable_entity_id
from .models import (
    ArpEntry,
    InterfaceStatus,
    LldpDiscoveryStatus,
    LocalEndpointStatus,
    MacTableEntry,
    SnmpInspectionStatus,
)
from .parsers.lldp import parse_lldp


_UNSUPPORTED = re.compile(
    r"%\s*(?:Invalid input|Ambiguous command|Incomplete command|Unrecognized command)",
    re.IGNORECASE,
)
_LLDP_DISABLED = re.compile(r"LLDP\s+is\s+not\s+enabled", re.IGNORECASE)


@dataclass(frozen=True)
class LocalAdapter:
    name: str
    ip: str
    netmask: str
    mac: str


def _locally_administered(value: str) -> bool:
    normalized = normalize_mac(value)
    return len(normalized) == 12 and bool(int(normalized[:2], 16) & 0x02)


def discover_local_adapters() -> list[LocalAdapter]:
    """Return active IPv4 adapters. psutil is packaged with the desktop sidecar."""
    try:
        import psutil
    except ImportError:  # pragma: no cover - packaging regression fallback
        return []

    stats = psutil.net_if_stats()
    adapters: list[LocalAdapter] = []
    for name, addresses in psutil.net_if_addrs().items():
        if not stats.get(name) or not stats[name].isup:
            continue
        mac = next(
            (item.address for item in addresses if item.family == psutil.AF_LINK),
            "",
        )
        for item in addresses:
            if item.family != socket.AF_INET or not item.netmask:
                continue
            try:
                address = ipaddress.ip_address(item.address)
            except ValueError:
                continue
            if address.is_loopback or address.is_link_local or not normalize_mac(mac):
                continue
            adapters.append(LocalAdapter(name=name, ip=item.address, netmask=item.netmask, mac=mac))
    return adapters


def _same_network(adapter: LocalAdapter, management_ip: str) -> bool:
    try:
        network = ipaddress.ip_network(f"{adapter.ip}/{adapter.netmask}", strict=False)
        return ipaddress.ip_address(management_ip) in network
    except ValueError:
        return False


def correlate_local_endpoint(
    *,
    management_ip: str,
    mac_entries: Sequence[MacTableEntry],
    arp_entries: Sequence[ArpEntry],
    interfaces: Sequence[InterfaceStatus],
    adapters: Sequence[LocalAdapter] | None = None,
) -> LocalEndpointStatus:
    """Identify this PC only when one physical access-port correlation is unique."""
    observed_adapters = list(adapters) if adapters is not None else discover_local_adapters()
    candidates = [item for item in observed_adapters if _same_network(item, management_ip)]
    if not candidates:
        return LocalEndpointStatus(
            state="unavailable",
            detail="No active local adapter on the switch management network could be inspected.",
        )

    mac_by_port: dict[str, set[str]] = {}
    ports_by_mac: dict[str, set[str]] = {}
    for entry in mac_entries:
        if entry.port.upper() == "CPU" or entry.vlan.lower() == "all":
            continue
        normalized = normalize_mac(entry.mac)
        if len(normalized) != 12:
            continue
        mac_by_port.setdefault(entry.port, set()).add(normalized)
        ports_by_mac.setdefault(normalized, set()).add(entry.port)

    matches: list[tuple[LocalAdapter, str]] = []
    randomised_match = False
    for adapter in candidates:
        normalized = normalize_mac(adapter.mac)
        ports = ports_by_mac.get(normalized, set())
        if ports and _locally_administered(adapter.mac):
            randomised_match = True
            continue
        if len(ports) == 1:
            matches.append((adapter, next(iter(ports))))

    if randomised_match:
        return LocalEndpointStatus(
            state="ambiguous",
            detail="A matching adapter uses a software-assigned MAC address, so SwitchOps will not claim a stable identity.",
        )
    if not matches:
        return LocalEndpointStatus(
            state="not-observed",
            detail="The active local adapter was not uniquely present in the switch MAC table.",
        )
    unique = {(adapter.ip, port) for adapter, port in matches}
    if len(unique) != 1:
        return LocalEndpointStatus(
            state="ambiguous",
            detail="More than one local adapter or switch path matched; no identity was assigned.",
        )

    adapter, port = matches[0]
    interface = next((item for item in interfaces if item.port.lower() == port.lower()), None)
    if interface is None or interface.status.lower() != "connected" or interface.vlan.lower() == "trunk":
        return LocalEndpointStatus(
            state="ambiguous",
            interface=port,
            detail="The MAC matched, but the switch port is not a connected access interface.",
        )
    if len(mac_by_port.get(port, set())) != 1:
        return LocalEndpointStatus(
            state="ambiguous",
            interface=port,
            detail="Other addresses are reachable through the same port, so direct attachment is uncertain.",
        )

    arp_for_ip = [entry for entry in arp_entries if entry.ip == adapter.ip]
    if arp_for_ip and any(normalize_mac(entry.mac) != normalize_mac(adapter.mac) for entry in arp_for_ip):
        return LocalEndpointStatus(
            state="ambiguous",
            interface=port,
            detail="The local IP and switch ARP evidence disagree, so no identity was assigned.",
        )
    return LocalEndpointStatus(
        state="confirmed",
        interface=port,
        ip=adapter.ip,
        identity_token=stable_entity_id(
            "local-host", "hardware-mac", normalize_mac(adapter.mac)
        ),
        detail=(
            "One active local adapter matched the only learned address on this connected access port. "
            "The hardware address is intentionally not exposed."
        ),
    )


def inspect_lldp(
    *, running_config: str | None, summary_output: str, detail_output: str
) -> LldpDiscoveryStatus:
    combined = f"{summary_output}\n{detail_output}"
    if _UNSUPPORTED.search(combined):
        return LldpDiscoveryStatus(
            state="unsupported",
            supported=False,
            enabled=None,
            detail="This IOS image does not support the allowlisted LLDP neighbour commands.",
        )
    configured = bool(
        running_config is not None
        and re.search(r"^\s*lldp\s+run\s*$", running_config, re.IGNORECASE | re.MULTILINE)
    )
    explicitly_disabled = bool(
        running_config is not None
        and re.search(r"^\s*no\s+lldp\s+run\s*$", running_config, re.IGNORECASE | re.MULTILINE)
    )
    disabled_banner = bool(_LLDP_DISABLED.search(combined))
    neighbors = parse_lldp(detail_output, summary_output)
    if configured or neighbors:
        return LldpDiscoveryStatus(
            state="enabled",
            supported=True,
            enabled=True,
            neighbors=neighbors,
            detail=f"LLDP is enabled and reported {len(neighbors)} direct neighbour(s).",
        )
    if explicitly_disabled or disabled_banner or (running_config is not None and not configured):
        return LldpDiscoveryStatus(
            state="disabled",
            supported=True,
            enabled=False,
            neighbors=[],
            detail="LLDP is supported but disabled; no LLDP identity evidence is being claimed.",
        )
    return LldpDiscoveryStatus(
        state="unknown", supported=True, enabled=None, detail="LLDP state could not be determined."
    )


def inspect_snmp_config(running_config: str) -> SnmpInspectionStatus:
    """Summarise existing SNMP configuration without retaining secret tokens."""
    community_lines = re.findall(
        r"^[ \t]*snmp-server[ \t]+community[ \t]+\S+(?:[ \t]+\S+)*[ \t]*$",
        running_config,
        re.IGNORECASE | re.MULTILINE,
    )
    read_write = sum(bool(re.search(r"(?:^|\s)RW(?:\s|$)", line, re.IGNORECASE)) for line in community_lines)
    read_only = len(community_lines) - read_write
    v3_users = len(re.findall(r"^\s*snmp-server\s+user\s+", running_config, re.IGNORECASE | re.MULTILINE))
    v3_groups = len(re.findall(r"^\s*snmp-server\s+group\s+\S+\s+v3\b", running_config, re.IGNORECASE | re.MULTILINE))
    trap_hosts = len(re.findall(r"^\s*snmp-server\s+host\s+", running_config, re.IGNORECASE | re.MULTILINE))
    versions: list[str] = []
    if community_lines:
        versions.append("v1/v2c")
    if v3_users or v3_groups:
        versions.append("v3")
    configured = bool(community_lines or v3_users or v3_groups or trap_hosts)
    detail = (
        "Existing SNMP configuration was detected read-only; SwitchOps did not use or change it."
        if configured
        else "No SNMP configuration was detected. SwitchOps continues to use the persistent SSH session."
    )
    return SnmpInspectionStatus(
        configured=configured,
        versions=versions,
        readOnlyCommunities=read_only,
        readWriteCommunities=read_write,
        v3Users=v3_users,
        trapHosts=trap_hosts,
        detail=detail,
    )
