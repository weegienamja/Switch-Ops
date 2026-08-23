"""Parse `show interfaces status`."""
from __future__ import annotations

import re
from typing import List

from ..command_registry import is_physical_interface, normalize_interface
from ..models import InterfaceStatus

_STATUS_VALUES = {
    "connected",
    "notconnect",
    "disabled",
    "err-disabled",
    "inactive",
    "monitoring",
    "suspended",
    "faulty",
    "sfpabsent",
}


def parse_interface_status(text: str) -> List[InterfaceStatus]:
    """Tolerant parser for `show interfaces status`.

    The IOS output uses fixed-ish columns; we slice by header position when
    possible and fall back to whitespace split.
    """
    interfaces: List[InterfaceStatus] = []
    lines = text.splitlines()
    header_idx = -1
    for i, ln in enumerate(lines):
        if ln.strip().startswith("Port") and "Name" in ln and "Status" in ln:
            header_idx = i
            break
    if header_idx == -1:
        return interfaces

    header = lines[header_idx]
    cols = {
        "Port": header.find("Port"),
        "Name": header.find("Name"),
        "Status": header.find("Status"),
        "Vlan": header.find("Vlan"),
        "Duplex": header.find("Duplex"),
        "Speed": header.find("Speed"),
        "Type": header.find("Type"),
    }

    def slice_col(line: str, key: str) -> str:
        start = cols[key]
        keys = ["Port", "Name", "Status", "Vlan", "Duplex", "Speed", "Type"]
        idx = keys.index(key)
        if idx + 1 < len(keys):
            end = cols[keys[idx + 1]]
            return line[start:end].strip() if start >= 0 else ""
        return line[start:].strip() if start >= 0 else ""

    for ln in lines[header_idx + 1:]:
        if not ln.strip():
            continue
        if not re.match(r"^[A-Za-z]", ln):
            continue
        parts = ln.split()
        status_index = next(
            (index for index, token in enumerate(parts[1:], start=1) if token.lower() in _STATUS_VALUES),
            None,
        )
        if status_index is not None and len(parts) >= status_index + 5:
            port = parts[0]
            name = " ".join(parts[1:status_index])
            status = parts[status_index]
            vlan = parts[status_index + 1]
            duplex = parts[status_index + 2]
            speed = parts[status_index + 3]
            type_ = " ".join(parts[status_index + 4:])
        else:
            port = slice_col(ln, "Port")
            name = slice_col(ln, "Name")
            status = slice_col(ln, "Status")
            vlan = slice_col(ln, "Vlan")
            duplex = slice_col(ln, "Duplex")
            speed = slice_col(ln, "Speed")
            type_ = slice_col(ln, "Type")

        if not port:
            continue
        try:
            canonical = normalize_interface(port)
        except Exception:
            continue
        if not is_physical_interface(canonical):
            continue
        interfaces.append(
            InterfaceStatus(
                port=port,
                name=name,
                status=status,
                vlan=vlan,
                duplex=duplex,
                speed=speed,
                type=type_,
                protected=False,
                policyState="UNMANAGED",
            )
        )
    return interfaces
