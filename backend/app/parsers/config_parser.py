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
    svi_addresses: List[Dict[str, str]] = []
    for match in re.finditer(
        r"^interface\s+(Vlan\d+)\s*$\n(?P<body>.*?)(?=^!\s*$|^interface\s+|\Z)",
        text,
        re.MULTILINE | re.DOTALL | re.IGNORECASE,
    ):
        address = re.search(
            r"^\s*ip address\s+(\S+)\s+(\S+)",
            match.group("body"),
            re.MULTILINE | re.IGNORECASE,
        )
        if address:
            svi_addresses.append({
                "interface": match.group(1),
                "ip": address.group(1),
                "mask": address.group(2),
            })
    out["svi_addresses"] = svi_addresses
    if len(svi_addresses) == 1:
        out["management_ip"] = svi_addresses[0]["ip"]
        out["management_mask"] = svi_addresses[0]["mask"]
        out["management_interface"] = svi_addresses[0]["interface"]
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
    # Structured interface policy for Lab Assurance. Values are emitted only
    # when an explicit configuration line proves them; defaults remain unknown.
    interfaces: Dict[str, Dict[str, object]] = {}
    for match in re.finditer(
        r"^interface\s+(\S+)\s*$\n(?P<body>.*?)(?=^!\s*$|^interface\s+|\Z)",
        text,
        re.MULTILINE | re.DOTALL | re.IGNORECASE,
    ):
        name = match.group(1)
        body = match.group("body")
        item: Dict[str, object] = {}
        explicit = {
            "description": r"^\s*description\s+(.+?)\s*$",
            "access_vlan": r"^\s*switchport access vlan\s+(\d+)\s*$",
            "native_vlan": r"^\s*switchport trunk native vlan\s+(\d+)\s*$",
            "allowed_vlans": r"^\s*switchport trunk allowed vlan(?:\s+add)?\s+(.+?)\s*$",
            "channel_group": r"^\s*channel-group\s+(\d+)\s+mode\s+(\S+)\s*$",
            "vrf": r"^\s*(?:vrf forwarding|ip vrf forwarding)\s+(\S+)\s*$",
            "ip_address": r"^\s*ip address\s+(\S+)\s+(\S+)\s*$",
        }
        for key, pattern in explicit.items():
            found = re.search(pattern, body, re.MULTILINE | re.IGNORECASE)
            if found:
                item[key] = list(found.groups()) if key == "ip_address" else found.group(1)
        mode = re.search(r"^\s*switchport mode\s+(access|trunk|dynamic\s+\S+)\s*$", body, re.MULTILINE | re.IGNORECASE)
        if mode:
            item["mode"] = mode.group(1).upper().replace(" ", "_")
        elif re.search(r"^\s*no switchport\s*$", body, re.MULTILINE | re.IGNORECASE):
            item["mode"] = "ROUTED"
        item["shutdown"] = bool(re.search(r"^\s*shutdown\s*$", body, re.MULTILINE | re.IGNORECASE))
        item["portfast"] = bool(re.search(r"^\s*spanning-tree portfast(?:\s+edge)?\s*$", body, re.MULTILINE | re.IGNORECASE))
        item["bpdu_guard"] = bool(re.search(r"^\s*spanning-tree bpduguard enable\s*$", body, re.MULTILINE | re.IGNORECASE))
        item["dhcp_snooping_trust"] = bool(re.search(r"^\s*ip dhcp snooping trust\s*$", body, re.MULTILINE | re.IGNORECASE))
        item["dai_trust"] = bool(re.search(r"^\s*ip arp inspection trust\s*$", body, re.MULTILINE | re.IGNORECASE))
        item["port_security"] = bool(re.search(r"^\s*switchport port-security(?:\s|$)", body, re.MULTILINE | re.IGNORECASE))
        item["dot1x"] = bool(re.search(r"^\s*(?:authentication port-control|dot1x pae)\s+", body, re.MULTILINE | re.IGNORECASE))
        interfaces[name] = item
    out["interfaces"] = interfaces

    cdp_feature: bool | None = None
    if re.search(r"^cdp run\s*$", text, re.MULTILINE | re.IGNORECASE):
        cdp_feature = True
    elif re.search(r"^no cdp run\s*$", text, re.MULTILINE | re.IGNORECASE):
        cdp_feature = False
    out["features"] = {
        "cdp": cdp_feature,
        "lldp": bool(re.search(r"^lldp run\s*$", text, re.MULTILINE | re.IGNORECASE)),
        "ip_routing": bool(re.search(r"^ip routing\s*$", text, re.MULTILINE | re.IGNORECASE)),
        "dhcp_snooping": bool(re.search(r"^ip dhcp snooping(?:\s|$)", text, re.MULTILINE | re.IGNORECASE)),
        "dai": bool(re.search(r"^ip arp inspection vlan\s+", text, re.MULTILINE | re.IGNORECASE)),
        "aaa": bool(re.search(r"^aaa new-model\s*$", text, re.MULTILINE | re.IGNORECASE)),
        "http_server": bool(re.search(r"^ip http server\s*$", text, re.MULTILINE | re.IGNORECASE)),
        "https_server": bool(re.search(r"^ip http secure-server\s*$", text, re.MULTILINE | re.IGNORECASE)),
        "ospf": bool(re.search(r"^router ospf\s+", text, re.MULTILINE | re.IGNORECASE)),
        "eigrp": bool(re.search(r"^router eigrp\s+", text, re.MULTILINE | re.IGNORECASE)),
        "bgp": bool(re.search(r"^router bgp\s+", text, re.MULTILINE | re.IGNORECASE)),
        "isis": bool(re.search(r"^router isis(?:\s|$)", text, re.MULTILINE | re.IGNORECASE)),
        "fhrp": bool(re.search(r"^\s*(?:standby|vrrp|glbp)\s+", text, re.MULTILINE | re.IGNORECASE)),
        "vrf": bool(re.search(r"^(?:vrf definition|ip vrf)\s+", text, re.MULTILINE | re.IGNORECASE)),
        "vxlan": bool(re.search(r"^interface nve\d+", text, re.MULTILINE | re.IGNORECASE)),
        "segment_routing": bool(re.search(r"^segment-routing(?:\s|$)", text, re.MULTILINE | re.IGNORECASE)),
        "srv6": bool(re.search(r"^\s*(?:segment-routing srv6|locator\s+\S+)", text, re.MULTILINE | re.IGNORECASE)),
        "ip_sla": bool(re.search(r"^ip sla\s+\d+", text, re.MULTILINE | re.IGNORECASE)),
        "bfd": bool(re.search(r"^\s*bfd(?:\s|$)", text, re.MULTILINE | re.IGNORECASE)),
        "evpn": bool(re.search(r"^\s*(?:address-family l2vpn evpn|evpn)(?:\s|$)", text, re.MULTILINE | re.IGNORECASE)),
    }
    return out
