"""Parse the chassis record from ``show inventory``."""
from __future__ import annotations

import re
from typing import Dict


def parse_inventory(text: str) -> Dict[str, str]:
    out: Dict[str, str] = {}
    name = re.search(r'^NAME:\s*"([^"]+)"', text, re.IGNORECASE | re.MULTILINE)
    descr = re.search(r'DESCR:\s*"([^"]+)"', text, re.IGNORECASE)
    record = re.search(
        r"PID:\s*([^,\r\n]+?)\s*,\s*VID:\s*([^,\r\n]*?)\s*,\s*SN:\s*(\S+)",
        text,
        re.IGNORECASE,
    )
    if name:
        out["name"] = name.group(1).strip()
    if descr:
        out["description"] = descr.group(1).strip()
    if record:
        out["pid"] = record.group(1).strip()
        out["vid"] = record.group(2).strip()
        out["serial"] = record.group(3).strip()
    return out
