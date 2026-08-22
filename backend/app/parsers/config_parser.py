"""Parse pieces of `show running-config` for the UI.

Returns hostname, management IP, gateway, interface descriptions, HTTP state.
Always redacts secret hashes for any UI preview.
"""
from __future__ import annotations

import re
from typing import Dict, List


_FULL_LINE_SECRET_PATTERNS: tuple[re.Pattern[str], ...] = (
    re.compile(r"^(\s*enable\s+(?:secret|password)(?:\s+\d+)?\s+)\S+.*$", re.IGNORECASE),
    re.compile(r"^(\s*username\s+\S+.*?\s+(?:secret|password)(?:\s+\d+)?\s+)\S+.*$", re.IGNORECASE),
    re.compile(r"^(\s*password(?:\s+\d+)?\s+)\S+.*$", re.IGNORECASE),
    re.compile(r"^(\s*(?:tacacs-server|radius-server)\s+key(?:\s+\d+)?\s+)\S+.*$", re.IGNORECASE),
    re.compile(r"^(\s*key-string(?:\s+\d+)?\s+)\S+.*$", re.IGNORECASE),
    re.compile(r"^(\s*ppp\s+chap\s+password(?:\s+\d+)?\s+)\S+.*$", re.IGNORECASE),
)

_TOKEN_SECRET_PATTERNS: tuple[tuple[re.Pattern[str], str], ...] = (
    (
        re.compile(r"^(\s*snmp-server\s+community\s+)\S+(.*)$", re.IGNORECASE),
        r"\1<redacted>\2",
    ),
    (
        re.compile(r"^(\s*crypto\s+isakmp\s+key\s+)\S+(\s+address\s+.*)$", re.IGNORECASE),
        r"\1<redacted>\2",
    ),
    (
        re.compile(r"^(\s*ntp\s+authentication-key\s+\d+\s+\S+(?:\s+\d+)?\s+)\S+(.*)$", re.IGNORECASE),
        r"\1<redacted>\2",
    ),
)


def redact_config(text: str) -> str:
    out_lines: List[str] = []
    for ln in text.splitlines():
        redacted = ln
        for pattern in _FULL_LINE_SECRET_PATTERNS:
            match = pattern.match(redacted)
            if match:
                redacted = f"{match.group(1)}<redacted>"
                break
        else:
            for pattern, replacement in _TOKEN_SECRET_PATTERNS:
                candidate, count = pattern.subn(replacement, redacted)
                if count:
                    redacted = candidate
                    break
        out_lines.append(redacted)
    return "\n".join(out_lines)


def parse_running_config(text: str) -> Dict[str, object]:
    out: Dict[str, object] = {}
    m = re.search(r"^hostname\s+(\S+)", text, re.MULTILINE)
    if m:
        out["hostname"] = m.group(1)
    m = re.search(
        r"interface Vlan1.*?ip address\s+(\S+)\s+(\S+)",
        text,
        re.DOTALL | re.IGNORECASE,
    )
    if m:
        out["management_ip"] = m.group(1)
        out["management_mask"] = m.group(2)
    m = re.search(r"^ip default-gateway\s+(\S+)", text, re.MULTILINE)
    if m:
        out["gateway"] = m.group(1)
    out["http_disabled"] = "no ip http server" in text
    out["https_disabled"] = "no ip http secure-server" in text
    descriptions: Dict[str, str] = {}
    shutdown: List[str] = []
    current_iface: str | None = None
    for ln in text.splitlines():
        m = re.match(r"^interface\s+(\S+)", ln)
        if m:
            current_iface = m.group(1)
            continue
        if current_iface and ln.startswith(" description "):
            descriptions[current_iface] = ln.strip()[len("description ") :]
        if current_iface and ln.strip() == "shutdown":
            shutdown.append(current_iface)
    out["descriptions"] = descriptions
    out["shutdown_interfaces"] = shutdown
    return out
