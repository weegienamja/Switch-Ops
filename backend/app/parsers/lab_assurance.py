"""Tolerant parsers for read-only Lab Assurance evidence.

These parsers return only values present in IOS output. Empty output therefore
means "not observed", never "disabled" or "healthy".
"""
from __future__ import annotations

import re
from typing import Any


def parse_vlan_list(value: str) -> list[int]:
    value = value.strip().lower()
    if not value or value in {"none", "all"}:
        return []
    result: set[int] = set()
    for token in value.split(","):
        token = token.strip()
        match = re.fullmatch(r"(\d+)-(\d+)", token)
        if match:
            start, end = int(match.group(1)), int(match.group(2))
            if 1 <= start <= end <= 4094 and end - start <= 4094:
                result.update(range(start, end + 1))
        elif token.isdigit() and 1 <= int(token) <= 4094:
            result.add(int(token))
    return sorted(result)


def parse_switchports(text: str) -> dict[str, dict[str, Any]]:
    result: dict[str, dict[str, Any]] = {}
    blocks = re.split(r"(?=^Name:\s*)", text, flags=re.MULTILINE | re.IGNORECASE)
    for block in blocks:
        name = re.search(r"^Name:\s*(\S+)", block, re.MULTILINE | re.IGNORECASE)
        if not name:
            continue
        item: dict[str, Any] = {"name": name.group(1)}
        enabled = re.search(r"^Switchport:\s*(\S+)", block, re.MULTILINE | re.IGNORECASE)
        operational = re.search(
            r"^Operational Mode:\s*(.+?)\s*$", block, re.MULTILINE | re.IGNORECASE
        )
        administrative = re.search(
            r"^Administrative Mode:\s*(.+?)\s*$", block, re.MULTILINE | re.IGNORECASE
        )
        access = re.search(
            r"^Access Mode VLAN:\s*(\d+)", block, re.MULTILINE | re.IGNORECASE
        )
        native = re.search(
            r"^Trunking Native Mode VLAN:\s*(\d+)", block, re.MULTILINE | re.IGNORECASE
        )
        allowed = re.search(
            r"^Trunking VLANs Enabled:\s*(.+?)\s*$", block, re.MULTILINE | re.IGNORECASE
        )
        item["switchport"] = enabled.group(1).lower() == "enabled" if enabled else None
        operational_mode = operational.group(1).lower() if operational else ""
        administrative_mode = administrative.group(1).lower() if administrative else ""
        mode = operational_mode
        if not any(token in mode for token in ("trunk", "access", "static", "dynamic", "routed")):
            mode = administrative_mode
        item["mode"] = (
            "TRUNK" if "trunk" in mode else
            "ACCESS" if "access" in mode or "static" in mode else
            "ROUTED" if item["switchport"] is False else
            "DYNAMIC" if "dynamic" in mode else "UNKNOWN"
        )
        item["access_vlan"] = int(access.group(1)) if access else None
        item["native_vlan"] = int(native.group(1)) if native else None
        item["allowed_vlans"] = parse_vlan_list(allowed.group(1)) if allowed else []
        result[item["name"]] = item
    return result


def parse_spanning_tree(text: str) -> list[dict[str, Any]]:
    instances: list[dict[str, Any]] = []
    for block in re.split(r"(?=^VLAN\d+\s*$)", text, flags=re.MULTILINE | re.IGNORECASE):
        vlan = re.search(r"^VLAN(\d+)\s*$", block, re.MULTILINE | re.IGNORECASE)
        if not vlan:
            continue
        root_address = re.search(
            r"Root ID.*?^\s*Address\s+(\S+)", block, re.MULTILINE | re.DOTALL | re.IGNORECASE
        )
        ports: list[dict[str, str]] = []
        for line in block.splitlines():
            row = re.match(r"^\s*(\S+)\s+(Root|Desg|Altn|Back|Mstr)\s+(FWD|BLK|LRN|LIS|DIS)\s+", line, re.IGNORECASE)
            if row:
                ports.append({"interface": row.group(1), "role": row.group(2).upper(), "state": row.group(3).upper()})
        instances.append(
            {
                "vlan": int(vlan.group(1)),
                "local_root": bool(re.search(r"This bridge is the root", block, re.IGNORECASE)),
                "root_address": root_address.group(1) if root_address else None,
                "ports": ports,
            }
        )
    return instances


def parse_etherchannels(text: str) -> list[dict[str, Any]]:
    groups: list[dict[str, Any]] = []
    for line in text.splitlines():
        match = re.match(r"^\s*(\d+)\s+(Po\d+)\(([^)]+)\)\s+(\S+)\s+(.+)$", line, re.IGNORECASE)
        if not match:
            continue
        members = [
            {"interface": name, "flags": flags}
            for name, flags in re.findall(r"(\S+?)\(([^)]+)\)", match.group(5))
        ]
        groups.append(
            {
                "group": int(match.group(1)),
                "port_channel": match.group(2),
                "flags": match.group(3),
                "protocol": match.group(4),
                "members": members,
            }
        )
    return groups


def parse_ip_interfaces(text: str) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for line in text.splitlines():
        match = re.match(
            r"^\s*(\S+)\s+(\S+)\s+\S+\s+\S+\s+(administratively down|up|down)\s+(up|down)\s*$",
            line,
            re.IGNORECASE,
        )
        if match:
            rows.append(
                {
                    "interface": match.group(1),
                    "address": None if match.group(2).lower() == "unassigned" else match.group(2),
                    "admin_up": match.group(3).lower() != "administratively down",
                    "oper_up": match.group(3).lower() == "up" and match.group(4).lower() == "up",
                }
            )
    return rows


def parse_default_route(text: str) -> dict[str, Any] | None:
    gateway = re.search(r"Gateway of last resort is\s+(\S+)\s+to network", text, re.IGNORECASE)
    if gateway:
        return {"next_hop": gateway.group(1), "source": "routing-table"}
    route = re.search(r"^\s*S\*\s+0\.0\.0\.0/0\s+\[[^]]+\]\s+via\s+(\S+)", text, re.MULTILINE)
    if route:
        return {"next_hop": route.group(1), "source": "routing-table"}
    return None


def parse_interface_rates(text: str) -> dict[str, dict[str, int]]:
    rates: dict[str, dict[str, int]] = {}
    current: str | None = None
    for line in text.splitlines():
        heading = re.match(r"^(\S+) is (?:up|down|administratively down), line protocol is", line, re.IGNORECASE)
        if heading:
            current = heading.group(1)
            rates.setdefault(current, {})
            continue
        if not current:
            continue
        input_rate = re.search(r"5 minute input rate\s+(\d+)\s+bits/sec", line, re.IGNORECASE)
        output_rate = re.search(r"5 minute output rate\s+(\d+)\s+bits/sec", line, re.IGNORECASE)
        if input_rate:
            rates[current]["input_bps"] = int(input_rate.group(1))
        if output_rate:
            rates[current]["output_bps"] = int(output_rate.group(1))
        output_drops = re.search(r"Total output drops:\s*(\d+)", line, re.IGNORECASE)
        input_drops = re.search(r"Input queue:\s*\d+/\d+/(\d+)/\d+", line, re.IGNORECASE)
        if output_drops:
            rates[current]["output_drops"] = int(output_drops.group(1))
        if input_drops:
            rates[current]["input_drops"] = int(input_drops.group(1))
    return rates


def parse_routing_neighbors(outputs: dict[str, str]) -> list[dict[str, str]]:
    neighbors: list[dict[str, str]] = []
    patterns = {
        "OSPF": re.compile(r"^\s*(\d{1,3}(?:\.\d{1,3}){3})\s+\d+\s+(?:FULL|2WAY)/\S+.*?\s+(\S+)\s*$", re.MULTILINE),
        "EIGRP": re.compile(r"^\s*\d+\s+(\d{1,3}(?:\.\d{1,3}){3})\s+(\S+)\s+", re.MULTILINE),
        "BGP": re.compile(r"^\s*(\d{1,3}(?:\.\d{1,3}){3})\s+\d+\s+\d+\s+", re.MULTILINE),
        "BFD": re.compile(r"^\s*(\d{1,3}(?:\.\d{1,3}){3})\s+\S+\s+(Up|Down)\b", re.MULTILINE | re.IGNORECASE),
    }
    keys = {"OSPF": "show_ip_ospf_neighbor", "EIGRP": "show_ip_eigrp_neighbors", "BGP": "show_bgp_ipv4_unicast_summary", "BFD": "show_bfd_neighbors"}
    for protocol, pattern in patterns.items():
        for match in pattern.finditer(outputs.get(keys[protocol], "")):
            neighbors.append({"protocol": protocol, "peer": match.group(1), "interface": match.group(2) if match.lastindex and match.lastindex >= 2 else ""})
    return neighbors
