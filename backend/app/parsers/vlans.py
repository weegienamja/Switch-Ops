"""Tolerant parser for ``show vlan brief``."""
from __future__ import annotations

import re
from typing import Any


def parse_vlans(text: str) -> list[dict[str, Any]]:
    vlans: list[dict[str, Any]] = []
    current: dict[str, Any] | None = None
    for line in text.splitlines():
        match = re.match(
            r"^\s*(\d+)\s+(\S+)\s+(active|act/unsup|suspend|shutdown)\s*(.*)$",
            line,
            re.IGNORECASE,
        )
        if match:
            ports = [port.strip() for port in match.group(4).split(",") if port.strip()]
            current = {
                "id": int(match.group(1)),
                "name": match.group(2),
                "status": match.group(3),
                "ports": ports,
            }
            vlans.append(current)
            continue
        if current is not None and re.match(r"^\s+Gi\S+", line, re.IGNORECASE):
            current["ports"].extend(
                port.strip() for port in line.split(",") if port.strip()
            )
    return vlans
