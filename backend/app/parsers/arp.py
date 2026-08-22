"""Parse ``show ip arp``.

ARP is the only table on this switch that ties an IP address to a hardware
address. Combined with the MAC table it answers a question nothing else can:
*which port is a given IP reachable through?*

That is weaker than CDP. ARP proves a path, never an identity, and an entry
only exists while the switch has had reason to talk to that address - the
default gateway routinely ages out of a managed switch's cache. Absence of an
entry means nothing at all, and the caller must treat it that way.
"""
from __future__ import annotations

import re
from typing import List

from ..models import ArpEntry


# Protocol  Address       Age (min)  Hardware Addr   Type   Interface
# Internet  192.0.2.10          -    0200.0000.0003  ARPA   Vlan1
_ROW = re.compile(
    r"^\s*Internet\s+"
    r"(?P<ip>\d{1,3}(?:\.\d{1,3}){3})\s+"
    r"(?P<age>-|\d+)\s+"
    r"(?P<mac>[0-9a-fA-F]{4}\.[0-9a-fA-F]{4}\.[0-9a-fA-F]{4})\s+"
    r"(?P<type>\S+)"
    r"(?:\s+(?P<interface>\S+))?\s*$",
    re.MULTILINE,
)


def parse_arp(text: str) -> List[ArpEntry]:
    """Tolerantly parse ARP rows. Unparseable lines are skipped, not raised."""
    entries: List[ArpEntry] = []
    if not text:
        return entries
    for match in _ROW.finditer(text):
        age_raw = match.group("age")
        entries.append(
            ArpEntry(
                ip=match.group("ip"),
                mac=match.group("mac").lower(),
                # "-" means the switch's own address, which never ages.
                ageMinutes=None if age_raw == "-" else int(age_raw),
                interface=match.group("interface") or "",
            )
        )
    return entries
