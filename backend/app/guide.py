"""Bounded, read-only Lab Guide operations and deterministic explanations."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
import re
from typing import Any, Callable

from .command_registry import (
    assert_interface_readable,
    resolve_read_command,
)
from .errors import CommandNotAllowedError
from .models import GuideOperation, GuideRunResult
from .parsers.cpu import parse_cpu
from .parsers.environment import parse_environment
from .parsers.errors import parse_interface_errors
from .parsers.interfaces import parse_interface_status
from .parsers.inventory import parse_inventory
from .parsers.logs import parse_logs
from .parsers.mac_table import parse_mac_table
from .parsers.memory import parse_memory
from .parsers.poe import parse_poe
from .parsers.version import parse_version
from .switch_client import SwitchClient
from .tools.backup import backup_running_config
from .tools.read_only import run_and_audit


@dataclass(frozen=True)
class GuideDefinition:
    id: str
    category: str
    title: str
    question: str
    what_it_tells_you: str
    symbols: tuple[str, ...]
    requires_interface: bool = False
    backup: bool = False

    def public(self) -> GuideOperation:
        return GuideOperation(
            id=self.id,
            category=self.category,
            title=self.title,
            question=self.question,
            whatItTellsYou=self.what_it_tells_you,
            commands=[resolve_read_command(symbol) for symbol in self.symbols],
            requiresInterface=self.requires_interface,
        )


GUIDE_DEFINITIONS: tuple[GuideDefinition, ...] = (
    GuideDefinition(
        "identify_switch",
        "GETTING STARTED",
        "Identify this switch",
        "What switch do I have?",
        "Shows the hardware model and installed Cisco IOS software.",
        ("show_version", "show_inventory"),
    ),
    GuideDefinition(
        "connected_ports",
        "GETTING STARTED",
        "Check connected ports",
        "Which ports are connected?",
        "Explains which physical interfaces have an Ethernet link and how each link negotiated.",
        ("show_interfaces_status",),
    ),
    GuideDefinition(
        "connected_devices",
        "GETTING STARTED",
        "Find connected devices",
        "What is connected to my switch?",
        "Correlates learned MAC-table entries with physical switch ports without guessing device identity.",
        ("show_interfaces_status", "show_mac_address_table"),
    ),
    GuideDefinition(
        "port_state",
        "TROUBLESHOOTING",
        "Explain a port state",
        "Why is this port down?",
        "Distinguishes an administratively disabled port from an enabled port with no physical link.",
        ("show_interfaces_status", "show_power_inline"),
        requires_interface=True,
    ),
    GuideDefinition(
        "port_errors",
        "TROUBLESHOOTING",
        "Check a port for errors",
        "Is this port getting errors?",
        "Shows cumulative interface counters and explains why change over time matters more than an old non-zero total.",
        ("show_interfaces_counters_errors",),
        requires_interface=True,
    ),
    GuideDefinition(
        "switch_load",
        "TROUBLESHOOTING",
        "Check switch load",
        "Is the switch under heavy load?",
        "Shows recent CPU use and processor-memory consumption using supported lightweight commands.",
        ("show_processes_cpu", "show_memory_statistics"),
    ),
    GuideDefinition(
        "poe_status",
        "TROUBLESHOOTING",
        "Inspect Power over Ethernet",
        "Is PoE working?",
        "Shows the PoE budget and which capable ports are currently supplying power.",
        ("show_power_inline",),
    ),
    GuideDefinition(
        "vlans",
        "NETWORKING",
        "Explore VLANs",
        "Show my VLANs",
        "Lists VLANs the switch reports and the access ports assigned to them.",
        ("show_vlan_brief",),
    ),
    GuideDefinition(
        "spanning_tree",
        "NETWORKING",
        "Inspect spanning tree",
        "What is the root bridge?",
        "Explains the observed spanning-tree root and port roles without changing the topology.",
        ("show_spanning_tree",),
    ),
    GuideDefinition(
        "neighbors",
        "NETWORKING",
        "Discover Cisco neighbours",
        "Show CDP neighbours",
        "Shows devices that advertise themselves through CDP. An empty result is valid.",
        ("show_cdp_neighbors_detail",),
    ),
    GuideDefinition(
        "temperature",
        "SWITCH",
        "Check the environment",
        "Show switch temperature",
        "Shows the current temperature and the switch-reported warning thresholds.",
        ("show_env_all",),
    ),
    GuideDefinition(
        "recent_logs",
        "SWITCH",
        "Review recent switch messages",
        "What has the switch logged?",
        "Parses recent IOS messages. Old messages are history and do not automatically become current health faults.",
        ("show_logging",),
    ),
    GuideDefinition(
        "backup_configuration",
        "SWITCH",
        "Back up the running configuration",
        "Save a local configuration backup",
        "Reads the running configuration into a private local file and returns only a redacted preview.",
        ("terminal_length_0", "show_running_config"),
        backup=True,
    ),
)

_BY_ID = {definition.id: definition for definition in GUIDE_DEFINITIONS}
_UNSUPPORTED = re.compile(
    r"%\s*(?:Invalid input|Ambiguous command|Incomplete command|Unrecognized command)",
    re.IGNORECASE,
)


def list_guide_operations() -> list[GuideOperation]:
    return [definition.public() for definition in GUIDE_DEFINITIONS]


def _short_interface(canonical: str) -> str:
    return canonical.replace("GigabitEthernet", "Gi")


def _parse_vlans(text: str) -> list[dict[str, Any]]:
    vlans: list[dict[str, Any]] = []
    for line in text.splitlines():
        match = re.match(r"^\s*(\d+)\s+(\S+)\s+(active|act/unsup|suspend|shutdown)\s*(.*)$", line, re.IGNORECASE)
        if not match:
            continue
        ports = [port.strip() for port in match.group(4).split(",") if port.strip()]
        vlans.append({
            "id": match.group(1),
            "name": match.group(2),
            "status": match.group(3),
            "ports": ports,
        })
    return vlans


def _parse_spanning_tree(text: str) -> dict[str, Any]:
    root_id = None
    local_is_root = "This bridge is the root" in text
    root_match = re.search(r"Root ID\s+Priority\s+(\d+).*?Address\s+([0-9a-f.]+)", text, re.IGNORECASE | re.DOTALL)
    if root_match:
        root_id = {"priority": int(root_match.group(1)), "address": root_match.group(2)}
    ports: list[dict[str, str]] = []
    for line in text.splitlines():
        match = re.match(
            r"^\s*(Gi\S+)\s+(\S+)\s+(FWD|BLK|LIS|LRN|DIS)\s+(\S+)",
            line,
            re.IGNORECASE,
        )
        if match:
            ports.append({
                "port": match.group(1),
                "role": match.group(2),
                "state": match.group(3).upper(),
                "cost": match.group(4),
            })
    return {"localSwitchIsRoot": local_is_root, "root": root_id, "ports": ports}


def _parse_cdp(text: str) -> list[dict[str, str | None]]:
    neighbors: list[dict[str, str | None]] = []
    blocks = re.split(r"-+\s*\n", text)
    for block in blocks:
        device = re.search(r"Device ID:\s*(.+)", block, re.IGNORECASE)
        if not device:
            continue
        local = re.search(r"Interface:\s*([^,\n]+)", block, re.IGNORECASE)
        remote = re.search(r"Port ID \(outgoing port\):\s*([^\n]+)", block, re.IGNORECASE)
        platform = re.search(r"Platform:\s*([^,\n]+)", block, re.IGNORECASE)
        neighbors.append({
            "deviceId": device.group(1).strip(),
            "localInterface": local.group(1).strip() if local else None,
            "remoteInterface": remote.group(1).strip() if remote else None,
            "platform": platform.group(1).strip() if platform else None,
        })
    return neighbors


def _explain_interface_status(status: str) -> str:
    value = status.lower()
    if value == "disabled":
        return "This interface has been administratively shut down. It will not establish a link until it is enabled."
    if value == "notconnect":
        return "This interface is enabled, but the switch does not currently detect an Ethernet link."
    if value == "connected":
        return "The switch detects an active Ethernet link on this interface."
    return "The switch reported a state that SwitchOps does not classify further."


def run_guide_operation(
    client: SwitchClient,
    *,
    operation_id: str,
    interface: str | None = None,
) -> GuideRunResult:
    definition = _BY_ID.get(operation_id)
    if definition is None:
        raise CommandNotAllowedError(f"Unknown Lab Guide operation: {operation_id!r}")
    selected: str | None = None
    if definition.requires_interface:
        if interface is None:
            raise CommandNotAllowedError(f"Lab Guide operation {operation_id!r} requires an interface.")
        selected = _short_interface(assert_interface_readable(interface))
    elif interface is not None:
        raise CommandNotAllowedError(f"Lab Guide operation {operation_id!r} does not accept an interface.")

    observed_at = datetime.now(timezone.utc)
    warnings: list[str] = []
    if definition.backup:
        backup = backup_running_config(client, actor="lab-guide")
        result = {
            "filename": backup.filename,
            "sizeBytes": backup.size_bytes,
            "timestamp": backup.timestamp.isoformat(),
            "redactedPreview": backup.redacted_preview,
        }
        explanation = "A private local backup was created. The displayed preview is redacted; no switch configuration was changed."
        return GuideRunResult(
            operation=definition.public(),
            observedAt=observed_at,
            result=result,
            explanation=explanation,
        )

    outputs: dict[str, str] = {}
    for symbol in definition.symbols:
        output = run_and_audit(client, symbol=symbol, actor="lab-guide")
        if _UNSUPPORTED.search(output):
            warnings.append(f"{resolve_read_command(symbol)} is unsupported by this IOS release.")
            output = ""
        outputs[symbol] = output

    result: dict[str, Any]
    explanation: str
    if operation_id == "identify_switch":
        version = parse_version(outputs["show_version"])
        inventory = parse_inventory(outputs["show_inventory"])
        result = {
            "hostname": version.get("hostname"),
            "model": inventory.get("pid") or version.get("model"),
            "hardwareRevision": inventory.get("vid") or version.get("hardware_revision"),
            "iosVersion": version.get("ios_version"),
            "iosImage": version.get("ios_image"),
            "uptime": version.get("uptime"),
        }
        explanation = f"This is {result['model'] or 'an unidentified Cisco switch'} running IOS {result['iosVersion'] or 'of an unknown version'}."
    elif operation_id == "connected_ports":
        interfaces = parse_interface_status(outputs["show_interfaces_status"])
        result = {"interfaces": [item.model_dump(by_alias=True) for item in interfaces]}
        connected = [item for item in interfaces if item.status == "connected"]
        disabled = [item for item in interfaces if item.status == "disabled"]
        waiting = [item for item in interfaces if item.status == "notconnect"]
        explanation = f"{len(connected)} port(s) have a link, {len(waiting)} are enabled without a link, and {len(disabled)} are administratively disabled."
    elif operation_id == "connected_devices":
        interfaces = {item.port: item for item in parse_interface_status(outputs["show_interfaces_status"])}
        entries = [entry for entry in parse_mac_table(outputs["show_mac_address_table"]) if entry.port.upper() != "CPU" and entry.vlan.lower() != "all"]
        result = {
            "devices": [
                {
                    "port": entry.port,
                    "vlan": entry.vlan,
                    "description": interfaces.get(entry.port).name if interfaces.get(entry.port) else "",
                    "evidence": "dynamic MAC-table entry",
                }
                for entry in entries
            ]
        }
        explanation = f"The switch currently has {len(entries)} learned endpoint record(s). A MAC entry shows where traffic was learned, not the exact device model."
    elif operation_id == "port_state":
        assert selected is not None
        interfaces = parse_interface_status(outputs["show_interfaces_status"])
        match = next((item for item in interfaces if item.port.lower() == selected.lower()), None)
        poe = parse_poe(outputs["show_power_inline"])
        poe_port = next((item for item in poe.ports if item.interface.lower() == selected.lower()), None)
        if match is None:
            result = {"interface": selected, "found": False}
            explanation = f"The switch did not return a status row for {selected}."
        else:
            result = {
                "interface": match.model_dump(by_alias=True),
                "poe": poe_port.model_dump(by_alias=True) if poe_port else None,
            }
            explanation = _explain_interface_status(match.status)
    elif operation_id == "port_errors":
        assert selected is not None
        counters = parse_interface_errors(outputs["show_interfaces_counters_errors"])
        match = next((item for item in counters if item.port.lower() == selected.lower()), None)
        result = {"interface": selected, "counters": match.model_dump(by_alias=True) if match else None}
        total = match.total if match else 0
        explanation = (
            f"{selected} currently reports {total} cumulative error(s). The total alone does not prove a current fault; the dashboard compares it with the previous observation."
        )
    elif operation_id == "switch_load":
        cpu = parse_cpu(outputs["show_processes_cpu"])
        memory = parse_memory(outputs["show_memory_statistics"])
        used_pct = None
        if memory.processor_total and memory.processor_used is not None:
            used_pct = round(memory.processor_used / memory.processor_total * 100, 1)
        result = {"cpu": cpu.model_dump(by_alias=True, exclude={"raw"}), "memory": memory.model_dump(by_alias=True, exclude={"raw"}), "memoryUsedPct": used_pct}
        explanation = f"Five-second CPU is {cpu.cpu_5sec if cpu.cpu_5sec is not None else 'unknown'}% and processor memory use is {used_pct if used_pct is not None else 'unknown'}%."
    elif operation_id == "poe_status":
        poe = parse_poe(outputs["show_power_inline"])
        result = poe.model_dump(by_alias=True)
        powered = [port for port in poe.ports if port.oper.lower() not in {"", "off", "n/a"}]
        explanation = f"The switch is using {poe.used_watts:.1f} W of {poe.available_watts:.1f} W and is supplying power on {len(powered)} port(s)."
    elif operation_id == "vlans":
        vlans = _parse_vlans(outputs["show_vlan_brief"])
        result = {"vlans": vlans}
        explanation = f"The switch reported {len(vlans)} VLAN(s). A VLAN separates one logical Ethernet network from another."
        if not vlans and not warnings:
            warnings.append("No VLAN rows were returned.")
    elif operation_id == "spanning_tree":
        result = _parse_spanning_tree(outputs["show_spanning_tree"])
        if result["localSwitchIsRoot"]:
            explanation = "This switch reports that it is the spanning-tree root for the displayed VLAN."
        elif result["root"]:
            explanation = "This switch reports another bridge as the spanning-tree root."
        else:
            explanation = "No spanning-tree root could be determined from the returned output."
    elif operation_id == "neighbors":
        neighbors = _parse_cdp(outputs["show_cdp_neighbors_detail"])
        result = {"neighbors": neighbors}
        explanation = f"CDP reported {len(neighbors)} neighbour(s). An empty result can simply mean no adjacent device is advertising CDP."
    elif operation_id == "temperature":
        environment = parse_environment(outputs["show_env_all"])
        result = environment.model_dump(by_alias=True, exclude={"raw"})
        explanation = f"The switch reports {environment.temperature_c if environment.temperature_c is not None else 'an unknown'} C and state {environment.state}."
    elif operation_id == "recent_logs":
        logs = parse_logs(outputs["show_logging"])
        result = {"entries": [entry.model_dump() for entry in logs.entries]}
        explanation = f"The switch returned {len(logs.entries)} parsed log message(s). These are historical records, not automatically current faults."
    else:  # pragma: no cover - every definition has an explicit handler
        raise CommandNotAllowedError(f"Lab Guide operation {operation_id!r} has no handler.")

    return GuideRunResult(
        operation=definition.public(),
        observedAt=observed_at,
        result=result,
        explanation=explanation,
        warnings=warnings,
    )
