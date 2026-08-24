"""Bounded read-only IOS/IOS-XE collection for Lab Assurance."""
from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from datetime import datetime, timezone

from .switch_client import SwitchClient
from .tools.read_only import run_and_audit


logger = logging.getLogger(__name__)
COMMAND_UNAVAILABLE = re.compile(
    r"%\s*(?:Invalid input|Ambiguous command|Incomplete command|Unrecognized command)",
    re.IGNORECASE,
)
FEATURE_UNSUPPORTED = re.compile(
    r"%?.*\b(?:feature|protocol|capability)\b.*\bnot supported\b|"
    r"%?.*\bnot supported on (?:this|the) platform\b",
    re.IGNORECASE,
)
INDETERMINATE_FAILURE = re.compile(
    r"%\s*(?:Authorization failed|Permission denied|Command (?:authorization )?failed|"
    r"Error|Cannot|Not enough|Unable to)",
    re.IGNORECASE,
)

LAB_COMMANDS: tuple[str, ...] = (
    "show_version",
    "show_inventory",
    "show_running_config",
    "show_ip_interface_brief",
    "show_interfaces_status",
    "show_interfaces",
    "show_interfaces_counters_errors",
    "show_interfaces_switchport",
    "show_interfaces_trunk",
    "show_power_inline",
    "show_mac_address_table",
    "show_ip_arp",
    "show_vlan_brief",
    "show_spanning_tree",
    "show_etherchannel_summary",
    "show_cdp_neighbors_detail",
    "show_lldp_neighbors",
    "show_lldp_neighbors_detail",
    "show_ip_route",
    "show_ip_protocols",
    "show_ip_ospf_neighbor",
    "show_ip_eigrp_neighbors",
    "show_bgp_ipv4_unicast_summary",
    "show_standby_brief",
    "show_vrf",
    "show_bfd_neighbors",
    "show_ip_sla_summary",
    "show_nve_peers",
    "show_bgp_l2vpn_evpn_summary",
    "show_segment_routing_mpls_sid_map",
    "show_segment_routing_srv6_locator",
)


@dataclass
class LabDeviceObservation:
    device_id: str
    configured_label: str
    primary: bool
    observed_at: datetime
    outputs: dict[str, str] = field(default_factory=dict)
    command_state: dict[str, str] = field(default_factory=dict)


def classify_command_output(output: str) -> tuple[str, str]:
    """Classify IOS output without turning command syntax into feature truth."""
    if FEATURE_UNSUPPORTED.search(output):
        return "unsupported", ""
    if COMMAND_UNAVAILABLE.search(output):
        return "unavailable", ""
    if INDETERMINATE_FAILURE.search(output):
        return "failed", ""
    if output.strip():
        return "observed", output
    return "empty", ""


def collect_lab_device(
    client: SwitchClient,
    *,
    device_id: str,
    label: str,
    primary: bool,
) -> LabDeviceObservation:
    observation = LabDeviceObservation(
        device_id=device_id,
        configured_label=label,
        primary=primary,
        observed_at=datetime.now(timezone.utc),
    )
    for symbol in LAB_COMMANDS:
        try:
            output = run_and_audit(client, symbol=symbol, actor="lab-assurance")
            state, usable_output = classify_command_output(output)
            observation.outputs[symbol] = usable_output
            observation.command_state[symbol] = state
        except Exception as exc:
            logger.warning(
                "Lab Assurance command %s failed on %s (%s)",
                symbol,
                device_id,
                type(exc).__name__,
            )
            observation.outputs[symbol] = ""
            observation.command_state[symbol] = "failed"
    return observation
