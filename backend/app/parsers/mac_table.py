"""Parse `show mac address-table`."""
from __future__ import annotations

import re
from typing import List

from ..models import MacTableEntry


def parse_mac_table(text: str) -> List[MacTableEntry]:
    entries: List[MacTableEntry] = []
    for ln in text.splitlines():
        m = re.match(
            r"^\s*(\d+|All)\s+([0-9a-fA-F.]{14})\s+(\S+)\s+(\S.*)$",
            ln,
        )
        if not m:
            continue
        entries.append(
            MacTableEntry(
                vlan=m.group(1),
                mac=m.group(2),
                type=m.group(3),
                port=m.group(4).strip(),
            )
        )
    return entries
