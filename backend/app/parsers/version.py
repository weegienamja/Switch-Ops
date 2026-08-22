"""Parse `show version` output."""
from __future__ import annotations

import re
from typing import Dict


def parse_version(text: str) -> Dict[str, str]:
    out: Dict[str, str] = {}
    m = re.search(r"Version\s+(\S+),\s+RELEASE SOFTWARE", text)
    if m:
        out["ios_version"] = m.group(1).rstrip(",")
    m = re.search(r"(\S+)\s+uptime is\s+(.+)", text)
    if m:
        out["hostname"] = m.group(1)
        out["uptime"] = m.group(2).strip()
    m = re.search(r"Model number\s*:\s*(\S+)", text)
    if m:
        out["model"] = m.group(1)
    m = re.search(r"System serial number\s*:\s*(\S+)", text)
    if m:
        out["serial"] = m.group(1)
    m = re.search(r'^System image file is\s+"([^"]+)"', text, re.MULTILINE | re.IGNORECASE)
    if m:
        out["ios_image"] = m.group(1).strip()
    m = re.search(r"^BOOTLDR:\s*(.+)$", text, re.MULTILINE | re.IGNORECASE)
    if m:
        out["bootloader"] = m.group(1).strip()
    m = re.search(
        r"^cisco\s+(\S+)\s+\([^)]*\)\s+processor\s+\(revision\s+([^)]+)\)\s+with\s+(\d+)K bytes of memory",
        text,
        re.MULTILINE | re.IGNORECASE,
    )
    if m:
        out.setdefault("model", m.group(1).strip())
        out["hardware_revision"] = m.group(2).strip()
        out["memory_kb"] = m.group(3)
    counts = re.findall(r"^(\d+)\s+(.+?)\s+interfaces?$", text, re.MULTILINE | re.IGNORECASE)
    if counts:
        out["interface_counts"] = ", ".join(f"{count} {kind.strip()}" for count, kind in counts)
    # Fallback for hostname if uptime line missing
    if "hostname" not in out:
        m = re.search(r"^(\S+)\s+uptime", text, re.MULTILINE)
        if m:
            out["hostname"] = m.group(1)
    return out
