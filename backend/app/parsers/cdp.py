"""Parse ``show cdp neighbors detail``.

CDP is the only source in this product that proves a *direct* physical
neighbour: the adjacent device announces itself on the wire. Everything else
(MAC-table entries, interface descriptions) is weaker evidence and is modelled
separately in ``topology.py``.
"""
from __future__ import annotations

import re
from typing import List

from ..models import CdpNeighbor


_DEVICE_ID = re.compile(r"^\s*Device ID:\s*(.+?)\s*$", re.IGNORECASE | re.MULTILINE)
_LOCAL = re.compile(r"^\s*Interface:\s*([^,\n]+)", re.IGNORECASE | re.MULTILINE)
_REMOTE = re.compile(r"Port ID \(outgoing port\):\s*([^\n]+)", re.IGNORECASE)
_PLATFORM = re.compile(r"^\s*Platform:\s*([^,\n]+)", re.IGNORECASE | re.MULTILINE)
_CAPABILITIES = re.compile(r"Capabilities:\s*([^\n]*)", re.IGNORECASE)
_IP = re.compile(r"IP(?:v4)? address:\s*([0-9]{1,3}(?:\.[0-9]{1,3}){3})", re.IGNORECASE)


def _short_interface(name: str) -> str:
    """Return the IOS short form used by ``show interfaces status`` rows."""
    value = name.strip()
    match = re.fullmatch(r"(?i)gigabitethernet\s*(\d+/\d+)", value)
    if match:
        return f"Gi{match.group(1)}"
    match = re.fullmatch(r"(?i)fastethernet\s*(\d+/\d+)", value)
    if match:
        return f"Fa{match.group(1)}"
    match = re.fullmatch(r"(?i)tengigabitethernet\s*(\d+/\d+/?\d*)", value)
    if match:
        return f"Te{match.group(1)}"
    return value


def parse_cdp(text: str) -> List[CdpNeighbor]:
    """Tolerantly parse CDP detail blocks.

    Old IOS separates neighbour blocks with a dashed rule. A block without a
    ``Device ID`` is ignored rather than raising, because 12.2 output can carry
    banners and totals between records.
    """
    neighbors: List[CdpNeighbor] = []
    if not text:
        return neighbors
    for block in re.split(r"^-{3,}\s*$", text, flags=re.MULTILINE):
        device = _DEVICE_ID.search(block)
        if not device:
            continue
        local = _LOCAL.search(block)
        remote = _REMOTE.search(block)
        platform = _PLATFORM.search(block)
        capabilities = _CAPABILITIES.search(block)
        ip = _IP.search(block)
        neighbors.append(
            CdpNeighbor(
                remoteName=device.group(1).strip(),
                localInterface=_short_interface(local.group(1)) if local else "",
                remoteInterface=remote.group(1).strip() if remote else None,
                platform=platform.group(1).strip() if platform else None,
                capabilities=[
                    token for token in (capabilities.group(1).split() if capabilities else []) if token
                ],
                ip=ip.group(1) if ip else None,
            )
        )
    return neighbors
