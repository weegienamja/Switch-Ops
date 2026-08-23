"""Tolerant parsers for Cisco IOS LLDP neighbour output."""
from __future__ import annotations

import re

from ..models import LldpNeighbor


_LOCAL = re.compile(r"^\s*Local (?:Intf|Interface):\s*(\S+)\s*$", re.IGNORECASE | re.MULTILINE)
_SYSTEM_NAME = re.compile(r"^\s*System Name:\s*(.+?)\s*$", re.IGNORECASE | re.MULTILINE)
_CHASSIS = re.compile(r"^\s*Chassis id:\s*(.+?)\s*$", re.IGNORECASE | re.MULTILINE)
_PORT = re.compile(r"^\s*Port id:\s*(.+?)\s*$", re.IGNORECASE | re.MULTILINE)
_DESCRIPTION = re.compile(
    r"^\s*System Description:\s*(.*?)(?=^\s*(?:Time remaining|System Capabilities|Enabled Capabilities|Management Addresses):|\Z)",
    re.IGNORECASE | re.MULTILINE | re.DOTALL,
)
_CAPABILITIES = re.compile(
    r"^\s*Enabled Capabilities:\s*(.*?)\s*$", re.IGNORECASE | re.MULTILINE
)
_IP = re.compile(r"^\s*(?:IP|IPV4):\s*(\d{1,3}(?:\.\d{1,3}){3})\s*$", re.IGNORECASE | re.MULTILINE)
_SUMMARY_ROW = re.compile(
    r"^\s*(?P<device>\S+)\s+(?P<local>(?:Gi|Fa|Te|Eth)\S+)\s+"
    r"(?P<hold>\d+)\s+(?P<caps>\S+(?:\s+\S+)*)\s+(?P<port>\S+)\s*$",
    re.IGNORECASE,
)


def _short_interface(name: str) -> str:
    value = name.strip()
    replacements = (
        (r"(?i)gigabitethernet\s*(\d+/\d+(?:/\d+)?)", "Gi"),
        (r"(?i)fastethernet\s*(\d+/\d+(?:/\d+)?)", "Fa"),
        (r"(?i)tengigabitethernet\s*(\d+/\d+(?:/\d+)?)", "Te"),
    )
    for pattern, prefix in replacements:
        match = re.fullmatch(pattern, value)
        if match:
            return f"{prefix}{match.group(1)}"
    return value


def _capability_tokens(value: str) -> list[str]:
    return [token for token in re.split(r"[\s,]+", value.strip()) if token and token != "-"]


def parse_lldp_detail(text: str) -> list[LldpNeighbor]:
    """Parse detail blocks, ignoring disabled/unsupported banners and noise."""
    neighbors: list[LldpNeighbor] = []
    if not text:
        return neighbors
    for block in re.split(r"^-{3,}\s*$", text, flags=re.MULTILINE):
        local = _LOCAL.search(block)
        if not local:
            continue
        name = _SYSTEM_NAME.search(block) or _CHASSIS.search(block)
        if not name:
            continue
        chassis = _CHASSIS.search(block)
        remote = _PORT.search(block)
        description = _DESCRIPTION.search(block)
        capabilities = _CAPABILITIES.search(block)
        ip = _IP.search(block)
        neighbors.append(
            LldpNeighbor(
                remoteName=name.group(1).strip(),
                chassisId=chassis.group(1).strip() if chassis else None,
                localInterface=_short_interface(local.group(1)),
                remoteInterface=remote.group(1).strip() if remote else None,
                systemDescription=(
                    " ".join(description.group(1).split()) if description and description.group(1).strip() else None
                ),
                capabilities=_capability_tokens(capabilities.group(1)) if capabilities else [],
                ip=ip.group(1) if ip else None,
            )
        )
    return neighbors


def parse_lldp_summary(text: str) -> list[LldpNeighbor]:
    """Parse the compact LLDP table when detail output is unavailable."""
    neighbors: list[LldpNeighbor] = []
    for line in text.splitlines():
        if not re.search(r"\d", line) or "local intf" in line.lower():
            continue
        match = _SUMMARY_ROW.match(line)
        if not match:
            continue
        neighbors.append(
            LldpNeighbor(
                remoteName=match.group("device"),
                localInterface=_short_interface(match.group("local")),
                remoteInterface=match.group("port"),
                capabilities=_capability_tokens(match.group("caps")),
            )
        )
    return neighbors


def parse_lldp(detail_text: str, summary_text: str = "") -> list[LldpNeighbor]:
    """Prefer richer detail output, falling back to the compact table."""
    detailed = parse_lldp_detail(detail_text)
    return detailed if detailed else parse_lldp_summary(summary_text)
