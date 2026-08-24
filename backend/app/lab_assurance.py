"""Evidence-backed v0.8 Lab Assurance analysis engine."""
from __future__ import annotations

import hashlib
import re
from collections import defaultdict, deque
from datetime import datetime, timezone
from typing import Any, Iterable

from .command_registry import short_interface
from .lab_collector import LAB_COMMANDS, LabDeviceObservation
from .models import (
    FailureScenario,
    LabAssuranceState,
    LabAssuranceSummary,
    LabCapability,
    LabDevice,
    LabEdge,
    LabEvidence,
    LabFinding,
    LabInterface,
    LabPath,
    LogicalNetwork,
    PathHop,
    PerformanceObservation,
)
from .parsers.arp import parse_arp
from .parsers.cdp import parse_cdp
from .parsers.config_parser import parse_running_config
from .parsers.errors import parse_interface_errors
from .parsers.interfaces import parse_interface_status
from .parsers.inventory import parse_inventory
from .parsers.lab_assurance import (
    parse_default_route,
    parse_etherchannels,
    parse_interface_rates,
    parse_ip_interfaces,
    parse_routing_neighbors,
    parse_spanning_tree,
    parse_switchports,
)
from .parsers.lldp import parse_lldp
from .parsers.mac_table import parse_mac_table
from .parsers.poe import parse_poe
from .parsers.version import parse_version
from .parsers.vlans import parse_vlans


def _token(*values: object, length: int = 16) -> str:
    source = "|".join(str(value).strip().casefold() for value in values)
    return hashlib.sha256(source.encode("utf-8")).hexdigest()[:length]


def _safe_parse(parser, text: str, fallback):
    try:
        return parser(text) if text else fallback
    except Exception:
        return fallback


def _short(name: str) -> str:
    try:
        return short_interface(name)
    except Exception:
        replacements = (
            ("GigabitEthernet", "Gi"),
            ("FastEthernet", "Fa"),
            ("TenGigabitEthernet", "Te"),
            ("TwentyFiveGigE", "Twe"),
            ("FortyGigabitEthernet", "Fo"),
            ("HundredGigE", "Hu"),
        )
        value = name.strip()
        for long_name, abbreviation in replacements:
            if value.casefold().startswith(long_name.casefold()):
                return abbreviation + value[len(long_name):]
        return value


def _speed_mbps(value: str) -> int | None:
    text = value.strip().lower().replace("a-", "")
    if text in {"", "auto", "--"}:
        return None
    match = re.fullmatch(r"([\d.]+)([gmt]?)", text)
    if not match:
        return None
    amount = float(match.group(1))
    factor = {"": 1, "m": 1, "g": 1000, "t": 1_000_000}[match.group(2)]
    return int(amount * factor)


def _evidence_id(device_id: str, symbol: str) -> str:
    return f"ev-{_token(device_id, symbol)}"


CAPABILITY_COMMANDS: dict[str, tuple[str, str]] = {
    "layer2-switching": ("Layer 2 switching", "show_vlan_brief"),
    "layer3-routing": ("Layer 3 routing", "show_ip_route"),
    "cdp": ("CDP adjacency", "show_cdp_neighbors_detail"),
    "lldp": ("LLDP adjacency", "show_lldp_neighbors_detail"),
    "vlans": ("VLAN inventory", "show_vlan_brief"),
    "trunks": ("802.1Q trunk state", "show_interfaces_trunk"),
    "spanning-tree": ("Spanning Tree", "show_spanning_tree"),
    "etherchannel": ("EtherChannel", "show_etherchannel_summary"),
    "poe": ("Power over Ethernet", "show_power_inline"),
    "routing-table": ("IPv4 routing table", "show_ip_route"),
    "ospf": ("OSPF neighbours", "show_ip_ospf_neighbor"),
    "eigrp": ("EIGRP neighbours", "show_ip_eigrp_neighbors"),
    "bgp": ("BGP IPv4 unicast", "show_bgp_ipv4_unicast_summary"),
    "bfd": ("BFD neighbours", "show_bfd_neighbors"),
    "fhrp": ("First-hop redundancy", "show_standby_brief"),
    "vrf": ("VRF inventory", "show_vrf"),
    "interface-telemetry": ("Interface rate telemetry", "show_interfaces"),
    "error-telemetry": ("Interface error telemetry", "show_interfaces_counters_errors"),
    "ip-sla": ("IP SLA", "show_ip_sla_summary"),
    "vxlan-nve": ("VXLAN/NVE", "show_nve_peers"),
    "evpn": ("BGP EVPN", "show_bgp_l2vpn_evpn_summary"),
    "segment-routing": ("Segment Routing MPLS", "show_segment_routing_mpls_sid_map"),
    "srv6": ("Segment Routing v6", "show_segment_routing_srv6_locator"),
}


CONFIG_FEATURES: dict[str, str] = {
    "dhcp-snooping": "DHCP snooping",
    "dai": "Dynamic ARP Inspection",
    "aaa": "AAA",
    "isis": "IS-IS",
}


def _build_evidence(observation: LabDeviceObservation) -> list[LabEvidence]:
    evidence: list[LabEvidence] = []
    for symbol in LAB_COMMANDS:
        state = observation.command_state.get(symbol, "failed")
        detail = {
            "observed": "The allowlisted command returned usable current output.",
            "unsupported": "IOS explicitly rejected this command form as unsupported.",
            "empty": "The command completed but returned no usable rows.",
            "failed": "The command could not be collected; no conclusion is drawn.",
        }[state]
        evidence.append(
            LabEvidence(
                id=_evidence_id(observation.device_id, symbol),
                deviceId=observation.device_id,
                kind=state.upper(),
                command=symbol,
                confidence="CONFIRMED" if state in {"observed", "unsupported"} else "UNKNOWN",
                observedAt=observation.observed_at,
                current=True,
                detail=detail,
            )
        )
    return evidence


def _capabilities(observation: LabDeviceObservation, config: dict[str, Any]) -> list[LabCapability]:
    result: list[LabCapability] = []
    features = config.get("features", {}) if isinstance(config, dict) else {}
    configured_by_capability = {
        "cdp": features.get("cdp"),
        "lldp": features.get("lldp"),
        "ospf": features.get("ospf"),
        "eigrp": features.get("eigrp"),
        "bgp": features.get("bgp"),
        "fhrp": features.get("fhrp"),
        "vrf": features.get("vrf"),
        "layer3-routing": features.get("ip_routing"),
        "ip-sla": features.get("ip_sla"),
        "bfd": features.get("bfd"),
        "vxlan-nve": features.get("vxlan"),
        "evpn": features.get("evpn"),
        "segment-routing": features.get("segment_routing"),
        "srv6": features.get("srv6"),
    }
    for capability_id, (name, symbol) in CAPABILITY_COMMANDS.items():
        command_state = observation.command_state.get(symbol, "failed")
        configured = configured_by_capability.get(capability_id)
        if command_state == "unsupported" and not configured:
            state = "UNSUPPORTED"
            detail = "The device explicitly rejected the capability's allowlisted observation command."
        elif command_state == "observed" or configured:
            state = "SUPPORTED"
            detail = "Current read-only output or explicit running configuration proves support."
        else:
            state = "UNKNOWN"
            detail = "Available evidence does not prove whether this capability is supported."
        result.append(
            LabCapability(
                id=f"cap-{_token(observation.device_id, capability_id)}",
                deviceId=observation.device_id,
                name=name,
                state=state,
                configured=configured if isinstance(configured, bool) else None,
                observed=command_state == "observed",
                detail=detail,
                evidenceIds=[_evidence_id(observation.device_id, symbol)],
            )
        )
    for feature_key, name in CONFIG_FEATURES.items():
        config_key = feature_key.replace("-", "_")
        configured = features.get(config_key)
        config_state = observation.command_state.get("show_running_config")
        result.append(
            LabCapability(
                id=f"cap-{_token(observation.device_id, feature_key)}",
                deviceId=observation.device_id,
                name=name,
                state="SUPPORTED" if configured else "UNKNOWN",
                configured=bool(configured) if config_state == "observed" else None,
                observed=bool(configured) if config_state == "observed" else None,
                detail=(
                    "Explicit running configuration proves the feature is in use."
                    if configured
                    else "No positive support evidence was observed; absence from configuration is not a platform verdict."
                ),
                evidenceIds=[_evidence_id(observation.device_id, "show_running_config")],
            )
        )
    return result


def _observation_parts(observation: LabDeviceObservation) -> dict[str, Any]:
    outputs = observation.outputs
    version = _safe_parse(parse_version, outputs.get("show_version", ""), {})
    inventory = _safe_parse(parse_inventory, outputs.get("show_inventory", ""), {})
    config = _safe_parse(parse_running_config, outputs.get("show_running_config", ""), {})
    interfaces = _safe_parse(parse_interface_status, outputs.get("show_interfaces_status", ""), [])
    errors = _safe_parse(parse_interface_errors, outputs.get("show_interfaces_counters_errors", ""), [])
    macs = _safe_parse(parse_mac_table, outputs.get("show_mac_address_table", ""), [])
    arp = _safe_parse(parse_arp, outputs.get("show_ip_arp", ""), [])
    vlans = _safe_parse(parse_vlans, outputs.get("show_vlan_brief", ""), [])
    poe = _safe_parse(parse_poe, outputs.get("show_power_inline", ""), None)
    cdp = _safe_parse(parse_cdp, outputs.get("show_cdp_neighbors_detail", ""), [])
    lldp = _safe_parse(
        lambda detail: parse_lldp(detail, outputs.get("show_lldp_neighbors", "")),
        outputs.get("show_lldp_neighbors_detail", ""),
        [],
    )
    return {
        "version": version,
        "inventory": inventory,
        "config": config,
        "interfaces": interfaces,
        "errors": errors,
        "macs": macs,
        "arp": arp,
        "vlans": vlans,
        "poe": poe,
        "cdp": cdp,
        "lldp": lldp,
        "switchports": parse_switchports(outputs.get("show_interfaces_switchport", "")),
        "stp": parse_spanning_tree(outputs.get("show_spanning_tree", "")),
        "etherchannels": parse_etherchannels(outputs.get("show_etherchannel_summary", "")),
        "ip_interfaces": parse_ip_interfaces(outputs.get("show_ip_interface_brief", "")),
        "default_route": parse_default_route(outputs.get("show_ip_route", "")),
        "rates": parse_interface_rates(outputs.get("show_interfaces", "")),
        "routing_neighbors": parse_routing_neighbors(outputs),
    }


def _device_role(capabilities: Iterable[str]) -> str:
    values = " ".join(capabilities).casefold()
    if "router" in values:
        return "ROUTER"
    if "switch" in values or "bridge" in values:
        return "SWITCH"
    if "wlan" in values or "access point" in values:
        return "ACCESS_POINT"
    return "UNKNOWN"


def _graph_from_observations(observations: list[LabDeviceObservation]):
    evidence: list[LabEvidence] = []
    devices: list[LabDevice] = []
    interfaces: list[LabInterface] = []
    edges: list[LabEdge] = []
    logical: list[LogicalNetwork] = []
    capabilities: list[LabCapability] = []
    parts_by_device: dict[str, dict[str, Any]] = {}
    hostname_to_device: dict[str, str] = {}

    for observation in observations:
        parts = _observation_parts(observation)
        parts_by_device[observation.device_id] = parts
        evidence.extend(_build_evidence(observation))
        capabilities.extend(_capabilities(observation, parts["config"]))
        version, config = parts["version"], parts["config"]
        label = str(config.get("hostname") or version.get("hostname") or observation.configured_label)
        hostname_to_device[label.casefold()] = observation.device_id
        failed = sum(1 for state in observation.command_state.values() if state == "failed")
        collection_state = (
            "FAILED" if failed == len(LAB_COMMANDS) else "PARTIAL" if failed else "CURRENT"
        )
        devices.append(
            LabDevice(
                id=observation.device_id,
                label=label,
                role="SWITCH",
                model=str(version.get("model") or parts["inventory"].get("pid") or "") or None,
                software=str(version.get("ios_version") or "") or None,
                primary=observation.primary,
                collectionState=collection_state,
                detail=(f"{len(LAB_COMMANDS) - failed} of {len(LAB_COMMANDS)} read-only observations completed."),
                evidenceIds=[_evidence_id(observation.device_id, "show_version")],
            )
        )

        config_interfaces = config.get("interfaces", {}) if isinstance(config, dict) else {}
        switchports = {_short(key): value for key, value in parts["switchports"].items()}
        config_interfaces = {_short(key): value for key, value in config_interfaces.items()}
        error_by_port = {_short(row.port): row.total for row in parts["errors"]}
        poe_by_port = {_short(row.interface): row for row in (parts["poe"].ports if parts["poe"] else [])}
        mac_count: dict[str, int] = defaultdict(int)
        for row in parts["macs"]:
            mac_count[_short(row.port)] += 1
        channel_by_port: dict[str, str] = {}
        for group in parts["etherchannels"]:
            for member in group["members"]:
                channel_by_port[_short(member["interface"])] = group["port_channel"]
        rates_by_port = {_short(key): value for key, value in parts["rates"].items()}
        for row in parts["interfaces"]:
            name = _short(row.port)
            config_item = config_interfaces.get(name, {})
            switchport = switchports.get(name, {})
            mode = switchport.get("mode") or config_item.get("mode") or (
                "TRUNK" if str(row.vlan).casefold() == "trunk" else
                "ACCESS" if str(row.vlan).isdigit() else "UNKNOWN"
            )
            status = row.status.casefold()
            interface_id = f"{observation.device_id}:{name}"
            poe_row = poe_by_port.get(name)
            rate = rates_by_port.get(name, {})
            speed_mbps = _speed_mbps(row.speed)
            peak_bps = max(rate.get("input_bps", 0), rate.get("output_bps", 0))
            utilization = (
                round(peak_bps / (speed_mbps * 1_000_000) * 100, 2)
                if speed_mbps and peak_bps
                else None
            )
            allowed = switchport.get("allowed_vlans") or []
            if not allowed and isinstance(config_item.get("allowed_vlans"), str):
                from .parsers.lab_assurance import parse_vlan_list
                allowed = parse_vlan_list(config_item["allowed_vlans"])
            interfaces.append(
                LabInterface(
                    id=interface_id,
                    deviceId=observation.device_id,
                    name=name,
                    adminState="DOWN" if status in {"disabled", "err-disabled"} else "UP",
                    operState="UP" if status == "connected" else "DOWN",
                    mode=mode if mode in {"ACCESS", "TRUNK", "ROUTED", "DYNAMIC"} else "UNKNOWN",
                    accessVlan=(switchport.get("access_vlan") or (int(row.vlan) if str(row.vlan).isdigit() else None)),
                    nativeVlan=switchport.get("native_vlan") or config_item.get("native_vlan"),
                    allowedVlans=allowed,
                    speedMbps=speed_mbps,
                    description=row.name or config_item.get("description"),
                    portChannel=channel_by_port.get(name),
                    poeWatts=poe_row.power_watts if poe_row else None,
                    learnedMacCount=mac_count.get(name, 0),
                    errorCount=error_by_port.get(name, 0),
                    dropCount=rate.get("input_drops", 0) + rate.get("output_drops", 0),
                    inputBps=rate.get("input_bps"),
                    outputBps=rate.get("output_bps"),
                    utilizationPercent=utilization,
                    evidenceIds=[
                        _evidence_id(observation.device_id, "show_interfaces_status"),
                        _evidence_id(observation.device_id, "show_running_config"),
                        _evidence_id(observation.device_id, "show_interfaces"),
                        _evidence_id(observation.device_id, "show_interfaces_counters_errors"),
                    ],
                )
            )

        for vlan in parts["vlans"]:
            vlan_id = int(vlan["id"])
            if vlan_id in {1002, 1003, 1004, 1005} or str(vlan.get("status", "")).casefold() == "act/unsup":
                continue
            member_ids = [f"{observation.device_id}:{_short(port)}" for port in vlan.get("ports", [])]
            trunks = [item.id for item in interfaces if item.device_id == observation.device_id and item.mode == "TRUNK" and (not item.allowed_vlans or vlan_id in item.allowed_vlans)]
            gateways = [
                observation.device_id
                for svi in config.get("svi_addresses", [])
                if str(svi.get("interface", "")).casefold() == f"vlan{vlan_id}".casefold()
            ]
            svi_config = config_interfaces.get(f"Vlan{vlan_id}", {})
            logical.append(
                LogicalNetwork(
                    id=f"vlan-{_token(observation.device_id, vlan_id)}",
                    vlanId=vlan_id,
                    name=str(vlan.get("name") or f"VLAN {vlan_id}"),
                    vrf=str(svi_config.get("vrf")) if svi_config.get("vrf") else None,
                    gatewayNodes=gateways,
                    memberInterfaces=member_ids,
                    trunkInterfaces=trunks,
                    isolationState="POLICY_UNKNOWN",
                    detail="VLAN membership is observed; inter-VLAN policy is not inferred from separation alone.",
                    evidenceIds=[_evidence_id(observation.device_id, "show_vlan_brief")],
                )
            )
        for routed in [item for item in interfaces if item.device_id == observation.device_id and item.mode == "ROUTED"]:
            config_item = config_interfaces.get(routed.name, {})
            logical.append(
                LogicalNetwork(
                    id=f"routed-{_token(routed.id)}",
                    name=f"Routed interface {routed.name}",
                    vrf=str(config_item.get("vrf")) if config_item.get("vrf") else None,
                    gatewayNodes=[observation.device_id],
                    memberInterfaces=[routed.id],
                    isolationState="POLICY_UNKNOWN",
                    detail="A routed interface is observed; reachability policy and remote subnet membership remain unknown.",
                    evidenceIds=[_evidence_id(observation.device_id, "show_running_config")],
                )
            )

    # Direct discovery edges. Exact hostname matches are the only automatic
    # merge into another explicitly observed device.
    pending_edges: list[tuple[str, str, str, str | None, str, str, list[str]]] = []
    for observation in observations:
        parts = parts_by_device[observation.device_id]
        discoveries = [
            (neighbor, "cdp", _evidence_id(observation.device_id, "show_cdp_neighbors_detail"))
            for neighbor in parts["cdp"]
        ] + [
            (neighbor, "lldp", _evidence_id(observation.device_id, "show_lldp_neighbors_detail"))
            for neighbor in parts["lldp"]
        ]
        grouped: dict[tuple[str, str], dict[str, Any]] = {}
        for neighbor, protocol, evidence_id in discoveries:
            key = (_short(neighbor.local_interface), neighbor.remote_name.casefold())
            item = grouped.setdefault(key, {"neighbor": neighbor, "protocols": [], "evidence": []})
            item["protocols"].append(protocol)
            item["evidence"].append(evidence_id)
        for (local_interface, _), item in grouped.items():
            neighbor = item["neighbor"]
            target_id = hostname_to_device.get(neighbor.remote_name.casefold())
            if target_id is None:
                target_id = f"discovered-{_token(neighbor.remote_name, neighbor.ip or '', neighbor.chassis_id if hasattr(neighbor, 'chassis_id') else '')}"
                if not any(device.id == target_id for device in devices):
                    devices.append(
                        LabDevice(
                            id=target_id,
                            label=neighbor.remote_name,
                            role=_device_role(neighbor.capabilities),
                            model=getattr(neighbor, "platform", None),
                            observed=True,
                            collectionState="NOT_COLLECTED",
                            detail="Announced directly by CDP/LLDP; this device was not independently collected.",
                            evidenceIds=item["evidence"],
                        )
                    )
            pending_edges.append((observation.device_id, target_id, local_interface, neighbor.remote_interface, neighbor.remote_name, "+".join(sorted(item["protocols"])), item["evidence"]))

    for source, target, local_port, remote_port, remote_name, protocols, evidence_ids in pending_edges:
        reciprocal = any(
            other_source == target
            and other_target == source
            and (not remote_port or _short(other_local) == _short(remote_port))
            for other_source, other_target, other_local, _other_remote, _name, _protocols, _evidence in pending_edges
        )
        edge_key = tuple(sorted((source, target))) + (local_port, remote_port or "")
        if any(edge.id == f"edge-{_token(*edge_key)}" for edge in edges):
            continue
        edges.append(
            LabEdge(
                id=f"edge-{_token(*edge_key)}",
                fromNodeId=source,
                toNodeId=target,
                fromInterface=local_port,
                toInterface=_short(remote_port) if remote_port else None,
                kind="PHYSICAL",
                state="PROVEN",
                confidence="CONFIRMED" if reciprocal else "HIGH",
                reciprocal=reciprocal,
                detail=(
                    f"Reciprocal discovery agrees across observed devices ({protocols})."
                    if reciprocal
                    else f"Direct {protocols.upper()} adjacency was observed from one side."
                ),
                evidenceIds=evidence_ids,
            )
        )
    # MAC evidence is useful but cannot prove a direct cable. Aggregate it per
    # access port so an uplink never explodes into one fake physical device per MAC.
    interface_by_id = {item.id: item for item in interfaces}
    for interface in list(interfaces):
        if interface.learned_mac_count < 1 or interface.mode == "TRUNK":
            continue
        endpoint_id = f"endpoint-{_token(interface.id)}"
        label = "Endpoint" if interface.learned_mac_count == 1 else f"{interface.learned_mac_count} learned endpoints"
        devices.append(
            LabDevice(
                id=endpoint_id,
                label=label,
                role="ENDPOINT",
                collectionState="NOT_COLLECTED",
                detail="MAC learning proves reachability through this port, not direct physical cabling.",
                evidenceIds=[_evidence_id(interface.device_id, "show_mac_address_table")],
            )
        )
        edges.append(
            LabEdge(
                id=f"edge-{_token(interface.id, endpoint_id)}",
                fromNodeId=interface.device_id,
                toNodeId=endpoint_id,
                fromInterface=interface.name,
                kind="L2_MEMBERSHIP",
                state="INFERRED",
                confidence="HIGH",
                reciprocal=False,
                detail="Current MAC-table learning places one or more identities behind this interface.",
                evidenceIds=[_evidence_id(interface.device_id, "show_mac_address_table")],
            )
        )
        if interface.access_vlan is not None:
            network_id = f"vlan-{_token(interface.device_id, interface.access_vlan)}"
            network = next((item for item in logical if item.id == network_id), None)
            if network is not None:
                network.endpoint_nodes.append(endpoint_id)

    # Default-gateway correlation uses the complete ARP -> MAC -> port chain.
    # The address and MAC are used transiently, reduced to an opaque node ID,
    # and never emitted as a persisted topology identity.
    for observation in observations:
        parts = parts_by_device[observation.device_id]
        gateway = parts["config"].get("gateway")
        if not gateway and parts["default_route"]:
            gateway = parts["default_route"].get("next_hop")
        if not gateway:
            continue
        gateway_id = f"gateway-{_token(observation.device_id, gateway)}"
        if not any(device.id == gateway_id for device in devices):
            devices.append(
                LabDevice(
                    id=gateway_id,
                    label="Default gateway",
                    role="GATEWAY",
                    collectionState="NOT_COLLECTED",
                    detail="A configured or routed default gateway is observed; its identity is privacy-minimized.",
                    evidenceIds=[_evidence_id(observation.device_id, "show_running_config")],
                )
            )
        arp_row = next((row for row in parts["arp"] if row.ip == gateway), None)
        gateway_port = None
        if arp_row:
            normalized_mac = re.sub(r"[^0-9a-f]", "", arp_row.mac.casefold())
            mac_row = next(
                (
                    row
                    for row in parts["macs"]
                    if re.sub(r"[^0-9a-f]", "", row.mac.casefold()) == normalized_mac
                ),
                None,
            )
            gateway_port = _short(mac_row.port) if mac_row else None
        gateway_evidence = [
            _evidence_id(observation.device_id, "show_running_config"),
            _evidence_id(observation.device_id, "show_ip_route"),
        ]
        if arp_row:
            gateway_evidence.extend(
                [
                    _evidence_id(observation.device_id, "show_ip_arp"),
                    _evidence_id(observation.device_id, "show_mac_address_table"),
                ]
            )
        edges.append(
            LabEdge(
                id=f"edge-{_token(observation.device_id, gateway_id, gateway_port or '')}",
                fromNodeId=observation.device_id,
                toNodeId=gateway_id,
                fromInterface=gateway_port,
                kind="L3_GATEWAY",
                state="INFERRED" if gateway_port else "UNKNOWN",
                confidence="HIGH" if gateway_port else "UNKNOWN",
                reciprocal=False,
                detail=(
                    "Default-gateway, ARP and MAC evidence agree on a forwarding interface."
                    if gateway_port
                    else "A default gateway exists, but current evidence does not place it on a physical interface."
                ),
                evidenceIds=sorted(set(gateway_evidence)),
            )
        )

    # Port-channel membership is configuration/state evidence, not a separate
    # physical neighbour.
    for observation in observations:
        for group in parts_by_device[observation.device_id]["etherchannels"]:
            channel_id = f"{observation.device_id}:{group['port_channel']}"
            for member in group["members"]:
                member_id = f"{observation.device_id}:{_short(member['interface'])}"
                if member_id not in interface_by_id:
                    continue
                edges.append(
                    LabEdge(
                        id=f"edge-{_token(member_id, channel_id)}",
                        fromNodeId=member_id,
                        toNodeId=channel_id,
                        kind="PORT_CHANNEL_MEMBER",
                        state="PROVEN",
                        confidence="CONFIRMED",
                        detail=f"EtherChannel flags reported as {member['flags']}.",
                        evidenceIds=[_evidence_id(observation.device_id, "show_etherchannel_summary")],
                    )
                )

    # Routing adjacencies remain distinct from physical links.
    for observation in observations:
        for neighbor in parts_by_device[observation.device_id]["routing_neighbors"]:
            peer_id = f"routing-peer-{_token(neighbor['protocol'], neighbor['peer'])}"
            if not any(device.id == peer_id for device in devices):
                devices.append(
                    LabDevice(
                        id=peer_id,
                        label=f"{neighbor['protocol']} peer",
                        role="ROUTER",
                        collectionState="NOT_COLLECTED",
                        detail="A current routing adjacency is observed; peer identity is privacy-minimized.",
                    )
                )
            symbol = {"OSPF": "show_ip_ospf_neighbor", "EIGRP": "show_ip_eigrp_neighbors", "BGP": "show_bgp_ipv4_unicast_summary", "BFD": "show_bfd_neighbors"}[neighbor["protocol"]]
            edges.append(
                LabEdge(
                    id=f"edge-{_token(observation.device_id, peer_id, neighbor['protocol'])}",
                    fromNodeId=observation.device_id,
                    toNodeId=peer_id,
                    fromInterface=_short(neighbor["interface"]) if neighbor["interface"] else None,
                    kind="ROUTING_ADJACENCY",
                    state="PROVEN",
                    confidence="HIGH",
                    detail=f"Current {neighbor['protocol']} neighbour output reports an adjacency.",
                    evidenceIds=[_evidence_id(observation.device_id, symbol)],
                )
            )

    return devices, interfaces, edges, logical, capabilities, evidence, parts_by_device


def _findings(devices, interfaces, edges, logical, capabilities, evidence, observations, parts_by_device):
    findings: list[LabFinding] = []
    unknown_capabilities = [item for item in capabilities if item.state == "UNKNOWN"]
    if unknown_capabilities:
        findings.append(
            LabFinding(
                id="finding-evidence-gaps",
                category="EVIDENCE",
                severity="UNKNOWN",
                confidence="UNKNOWN",
                title="Some design questions remain unproven",
                detail=f"{len(unknown_capabilities)} capability observations are unknown, so related checks stay inconclusive.",
                consequence="A missing command or unsupported parser can hide both strengths and risks.",
                remediation="Review Capabilities and collect the missing read-only evidence where the platform supports it.",
                affectedIds=sorted({item.device_id for item in unknown_capabilities}),
                evidenceIds=[value for item in unknown_capabilities for value in item.evidence_ids],
            )
        )

    physical = [edge for edge in edges if edge.kind == "PHYSICAL"]
    for device in [item for item in devices if item.collection_state in {"CURRENT", "PARTIAL"}]:
        attached = [edge for edge in physical if device.id in {edge.from_node_id, edge.to_node_id}]
        if len(attached) == 0:
            findings.append(
                LabFinding(
                    id=f"finding-uplink-unknown-{device.id}",
                    category="RESILIENCY",
                    severity="UNKNOWN",
                    confidence="UNKNOWN",
                    title="No proven uplink for a collected device",
                    detail="Neither CDP nor LLDP produced a current physical adjacency for this collected device.",
                    consequence="SwitchOps cannot simulate upstream loss reliably.",
                    remediation="Enable a supported discovery protocol or add another independently observed device.",
                    affectedIds=[device.id],
                )
            )
        elif len(attached) == 1:
            findings.append(
                LabFinding(
                    id=f"finding-single-uplink-{device.id}",
                    category="RESILIENCY",
                    severity="WARNING",
                    confidence="HIGH",
                    title="One observed infrastructure path for a collected device",
                    detail="The current graph contains one direct discovered relationship for this device.",
                    consequence="Loss of that relationship may isolate the device or everything behind it.",
                    remediation="Confirm whether a second independent uplink is intended and observable.",
                    affectedIds=[device.id, attached[0].id],
                    evidenceIds=attached[0].evidence_ids,
                )
            )

    active_unused = [item for item in interfaces if item.admin_state == "UP" and item.oper_state == "DOWN"]
    if active_unused:
        findings.append(
            LabFinding(
                id="finding-unused-active-ports",
                category="SECURITY",
                severity="NOTICE",
                confidence="CONFIRMED",
                title=f"{len(active_unused)} active ports have no link",
                detail="Current status shows administratively enabled interfaces without an operational link.",
                consequence="Unused active access ports increase the accidental or unauthorized attachment surface.",
                remediation="Review intent before protecting or disabling unused ports through normal change control.",
                affectedIds=[item.id for item in active_unused],
                evidenceIds=sorted({value for item in active_unused for value in item.evidence_ids}),
            )
        )

    error_ports = [item for item in interfaces if item.error_count > 0]
    if error_ports:
        findings.append(
            LabFinding(
                id="finding-interface-errors",
                category="PERFORMANCE",
                severity="WARNING",
                confidence="CONFIRMED",
                title="Interface errors are accumulating",
                detail=f"{len(error_ports)} interfaces have non-zero current error counters.",
                consequence="Layer-1 faults can make a link appear up while service is degraded.",
                remediation="Inspect cabling, optics, speed/duplex negotiation and counter changes.",
                affectedIds=[item.id for item in error_ports],
                evidenceIds=sorted({value for item in error_ports for value in item.evidence_ids}),
            )
        )

    dropping_ports = [item for item in interfaces if item.drop_count > 0]
    if dropping_ports:
        findings.append(
            LabFinding(
                id="finding-interface-drops",
                category="PERFORMANCE",
                severity="WARNING",
                confidence="CONFIRMED",
                title="Interface queue drops are observed",
                detail=f"{len(dropping_ports)} interfaces report non-zero input or output drops.",
                consequence="A link can remain operational while congestion discards traffic and degrades service.",
                remediation="Compare utilization, queue policy, traffic bursts and counter changes over time.",
                affectedIds=[item.id for item in dropping_ports],
                evidenceIds=sorted({value for item in dropping_ports for value in item.evidence_ids}),
            )
        )
    utilized_ports = [
        item
        for item in interfaces
        if item.utilization_percent is not None and item.utilization_percent >= 80
    ]
    if utilized_ports:
        findings.append(
            LabFinding(
                id="finding-high-utilization",
                category="CAPACITY",
                severity="WARNING",
                confidence="CONFIRMED",
                title="Five-minute interface utilization exceeds 80%",
                detail=f"{len(utilized_ports)} interfaces are at or above the current high-utilization threshold.",
                consequence="Sustained load leaves less room for bursts and can lead to queueing or drops.",
                remediation="Correlate rates with service probes and drops before changing capacity or policy.",
                affectedIds=[item.id for item in utilized_ports],
                evidenceIds=sorted({value for item in utilized_ports for value in item.evidence_ids}),
            )
        )

    for observation in observations:
        parts = parts_by_device[observation.device_id]
        features = parts["config"].get("features", {})
        if observation.command_state.get("show_running_config") == "observed":
            for feature_key, title in (
                ("dhcp_snooping", "DHCP snooping is not observed"),
                ("dai", "Dynamic ARP Inspection is not observed"),
            ):
                if not features.get(feature_key):
                    findings.append(
                        LabFinding(
                            id=f"finding-{feature_key}-{observation.device_id}",
                            category="SECURITY",
                            severity="NOTICE",
                            confidence="CONFIRMED",
                            title=title,
                            detail="The complete current running configuration contains no explicit enablement for this protection.",
                            consequence="The observed access layer may rely on other controls that SwitchOps has not proved.",
                            remediation="Review whether this control is appropriate for the lab before planning any configuration.",
                            affectedIds=[observation.device_id],
                            evidenceIds=[_evidence_id(observation.device_id, "show_running_config")],
                        )
                    )
        if features.get("http_server") or features.get("https_server"):
            findings.append(
                LabFinding(
                    id=f"finding-management-http-{observation.device_id}",
                    category="SECURITY",
                    severity="WARNING",
                    confidence="CONFIRMED",
                    title="IOS web management is enabled",
                    detail="Running configuration explicitly enables an HTTP or HTTPS management service.",
                    consequence="The management plane has an additional reachable service and authentication surface.",
                    remediation="Confirm the service is required, restricted and hardened; otherwise remove it through controlled change.",
                    affectedIds=[observation.device_id],
                    evidenceIds=[_evidence_id(observation.device_id, "show_running_config")],
                )
            )
        poe = parts["poe"]
        if poe and poe.available_watts > 0:
            remaining_percent = poe.remaining_watts / poe.available_watts * 100
            if remaining_percent < 20:
                findings.append(
                    LabFinding(
                        id=f"finding-poe-headroom-{observation.device_id}",
                        category="CAPACITY",
                        severity="WARNING",
                        confidence="CONFIRMED",
                        title="PoE headroom is below 20%",
                        detail=f"The device reports {poe.remaining_watts:.1f} W remaining of {poe.available_watts:.1f} W.",
                        consequence="A new AP, phone or transient draw increase may exceed the available power budget.",
                        remediation="Validate worst-case powered-device demand or add PoE capacity.",
                        affectedIds=[observation.device_id],
                        evidenceIds=[_evidence_id(observation.device_id, "show_power_inline")],
                    )
                )
            else:
                findings.append(
                    LabFinding(
                        id=f"finding-poe-reserve-{observation.device_id}",
                        category="CAPACITY",
                        severity="NOTICE",
                        confidence="CONFIRMED",
                        title="PoE reserve is currently above 20%",
                        detail=f"The device reports {poe.remaining_watts:.1f} W remaining of {poe.available_watts:.1f} W.",
                        consequence="Current draw has observed reserve, but this is not a worst-case powered-device budget.",
                        remediation="Compare maximum device demand before adding powered endpoints.",
                        affectedIds=[observation.device_id],
                        evidenceIds=[_evidence_id(observation.device_id, "show_power_inline")],
                    )
                )
        for group in parts["etherchannels"]:
            unhealthy = [member for member in group["members"] if "P" not in member["flags"].upper()]
            if unhealthy:
                findings.append(
                    LabFinding(
                        id=f"finding-etherchannel-{_token(observation.device_id, group['port_channel'])}",
                        category="RESILIENCY",
                        severity="WARNING",
                        confidence="CONFIRMED",
                        title=f"{group['port_channel']} has an unbundled member",
                        detail="EtherChannel summary does not mark every configured member as bundled.",
                        consequence="Expected link redundancy or aggregate capacity is not fully available.",
                        remediation="Compare member configuration and peer state without changing either side automatically.",
                        affectedIds=[f"{observation.device_id}:{_short(member['interface'])}" for member in unhealthy],
                        evidenceIds=[_evidence_id(observation.device_id, "show_etherchannel_summary")],
                    )
                )

        device_interfaces = [item for item in interfaces if item.device_id == observation.device_id]
        physical_uplink_ports = {
            _short(edge.from_interface or "")
            for edge in physical
            if edge.from_node_id == observation.device_id and edge.from_interface
        } | {
            _short(edge.to_interface or "")
            for edge in physical
            if edge.to_node_id == observation.device_id and edge.to_interface
        }
        uplink_capacity = sum(
            item.speed_mbps or 0
            for item in device_interfaces
            if item.name in physical_uplink_ports and item.oper_state == "UP"
        )
        access_capacity = sum(
            item.speed_mbps or 0
            for item in device_interfaces
            if item.mode == "ACCESS" and item.oper_state == "UP"
        )
        if uplink_capacity and access_capacity > uplink_capacity * 4:
            findings.append(
                LabFinding(
                    id=f"finding-oversubscription-{observation.device_id}",
                    category="CAPACITY",
                    severity="NOTICE",
                    confidence="HIGH",
                    title="Obvious access-to-uplink capacity oversubscription",
                    detail=f"Observed active access link rates total {access_capacity} Mbps versus {uplink_capacity} Mbps of discovered uplink rate.",
                    consequence="Simultaneous endpoint demand can exceed the observed upstream link capacity.",
                    remediation="Validate actual utilization and traffic patterns; oversubscription is not automatically a fault.",
                    affectedIds=[observation.device_id],
                    evidenceIds=[
                        _evidence_id(observation.device_id, "show_interfaces_status"),
                        _evidence_id(observation.device_id, "show_cdp_neighbors_detail"),
                        _evidence_id(observation.device_id, "show_lldp_neighbors_detail"),
                    ],
                )
            )
        local_roots = [instance for instance in parts["stp"] if instance["local_root"]]
        if local_roots:
            findings.append(
                LabFinding(
                    id=f"finding-stp-root-{observation.device_id}",
                    category="LAYER2",
                    severity="NOTICE",
                    confidence="CONFIRMED",
                    title=f"A collected device is STP root for {len(local_roots)} VLANs",
                    detail="Current spanning-tree output explicitly identifies this bridge as root.",
                    consequence="Failure of this device changes Layer-2 forwarding for those VLANs.",
                    remediation="Confirm the root role and secondary-root placement match the intended failure domain.",
                    affectedIds=[observation.device_id],
                    evidenceIds=[_evidence_id(observation.device_id, "show_spanning_tree")],
                )
            )

        config_interfaces = parts["config"].get("interfaces", {})
        access_configs = [value for value in config_interfaces.values() if value.get("mode") == "ACCESS"]
        if access_configs:
            for key, title in (("bpdu_guard", "BPDU Guard"), ("port_security", "port security / 802.1X")):
                enabled = sum(1 for value in access_configs if value.get(key) or (key == "port_security" and value.get("dot1x")))
                if enabled < len(access_configs):
                    findings.append(
                        LabFinding(
                            id=f"finding-{key}-{observation.device_id}",
                            category="SECURITY",
                            severity="NOTICE",
                            confidence="CONFIRMED",
                            title=f"{title} is not explicit on every observed access port",
                            detail=f"Explicit configuration was observed on {enabled} of {len(access_configs)} access-mode interfaces.",
                            consequence="Access-edge protection coverage is incomplete or relies on defaults not proven here.",
                            remediation="Review the intended edge-port policy; do not assume global defaults from absent interface lines.",
                            affectedIds=[observation.device_id],
                            evidenceIds=[_evidence_id(observation.device_id, "show_running_config")],
                        )
                    )

    # Compare only reciprocal, explicit port pairs. Anything weaker remains
    # ambiguous rather than manufacturing a native/allowed VLAN mismatch.
    by_interface = {item.id: item for item in interfaces}
    for edge in [item for item in physical if item.reciprocal and item.to_interface]:
        left = by_interface.get(f"{edge.from_node_id}:{_short(edge.from_interface or '')}")
        right = by_interface.get(f"{edge.to_node_id}:{_short(edge.to_interface or '')}")
        if not left or not right:
            continue
        if left.mode != right.mode and "UNKNOWN" not in {left.mode, right.mode}:
            findings.append(
                LabFinding(
                    id=f"finding-mode-mismatch-{edge.id}",
                    category="LAYER2",
                    severity="WARNING",
                    confidence="CONFIRMED",
                    title="Reciprocal link mode mismatch",
                    detail=f"{left.name} reports {left.mode}; peer {right.name} reports {right.mode}.",
                    consequence="VLAN forwarding across this link may be incomplete or unsafe.",
                    remediation="Review both sides together through change control.",
                    affectedIds=[left.id, right.id],
                    evidenceIds=edge.evidence_ids + left.evidence_ids + right.evidence_ids,
                )
            )
        if left.mode == right.mode == "TRUNK" and left.native_vlan and right.native_vlan and left.native_vlan != right.native_vlan:
            findings.append(
                LabFinding(
                    id=f"finding-native-mismatch-{edge.id}",
                    category="LAYER2",
                    severity="CRITICAL",
                    confidence="CONFIRMED",
                    title="Reciprocal trunk native VLAN mismatch",
                    detail=f"The two observed sides report native VLANs {left.native_vlan} and {right.native_vlan}.",
                    consequence="Untagged traffic can enter different broadcast domains across the link.",
                    remediation="Reconcile intended native VLANs on both ends in one controlled plan.",
                    affectedIds=[left.id, right.id],
                    evidenceIds=edge.evidence_ids + left.evidence_ids + right.evidence_ids,
                )
            )
        if left.mode == right.mode == "TRUNK" and left.allowed_vlans and right.allowed_vlans and set(left.allowed_vlans) != set(right.allowed_vlans):
            findings.append(
                LabFinding(
                    id=f"finding-allowed-vlan-mismatch-{edge.id}",
                    category="LAYER2",
                    severity="WARNING",
                    confidence="CONFIRMED",
                    title="Reciprocal trunk allowed-VLAN mismatch",
                    detail="Both observed sides provide explicit allowed-VLAN lists and the sets differ.",
                    consequence="Some broadcast domains may be forwarded in only one direction or fail across the trunk.",
                    remediation="Compare the intended VLAN scope and reconcile both ends together.",
                    affectedIds=[left.id, right.id],
                    evidenceIds=edge.evidence_ids + left.evidence_ids + right.evidence_ids,
                )
            )
    return findings


def devices_by_id(devices: list[LabDevice]) -> dict[str, LabDevice]:
    return {item.id: item for item in devices}


def _reachable(start: str, edges: list[LabEdge], *, omitted_node: str | None = None, omitted_edge: str | None = None) -> set[str]:
    adjacency: dict[str, set[str]] = defaultdict(set)
    for edge in edges:
        if edge.id == omitted_edge or edge.kind in {"PORT_CHANNEL_MEMBER", "EXPECTED"}:
            continue
        if omitted_node in {edge.from_node_id, edge.to_node_id}:
            continue
        adjacency[edge.from_node_id].add(edge.to_node_id)
        adjacency[edge.to_node_id].add(edge.from_node_id)
    seen = {start}
    queue = deque([start])
    while queue:
        node = queue.popleft()
        for neighbor in adjacency[node]:
            if neighbor not in seen:
                seen.add(neighbor)
                queue.append(neighbor)
    return seen


def _failures(devices: list[LabDevice], interfaces: list[LabInterface], edges: list[LabEdge]) -> list[FailureScenario]:
    primary = next((item for item in devices if item.primary), None)
    if not primary:
        return []
    all_ids = {item.id for item in devices}
    result: list[FailureScenario] = []
    for edge in [item for item in edges if item.kind in {"PHYSICAL", "ROUTING_ADJACENCY"}]:
        reachable = _reachable(primary.id, edges, omitted_edge=edge.id)
        affected = sorted(all_ids - reachable)
        target_kind = "ADJACENCY" if edge.kind == "ROUTING_ADJACENCY" else "UPLINK"
        result.append(
            FailureScenario(
                id=f"failure-{edge.id}",
                targetId=edge.id,
                targetKind=target_kind,
                title=f"Loss of {edge.from_interface or edge.kind.lower()}",
                confidence=edge.confidence,
                consequences=(
                    [f"{len(affected)} graph nodes lose their observed path from the primary collected device."]
                    if affected else ["The observed graph retains another path; service failover itself is not proven."]
                ),
                affectedIds=affected,
                controlImpact=(
                    "SwitchOps may lose observation/control reachability to affected devices."
                    if affected else "No control-path loss is proven by the current graph."
                ),
                evidenceIds=edge.evidence_ids,
            )
        )
    for device in [item for item in devices if item.id != primary.id and item.role in {"SWITCH", "ROUTER", "GATEWAY", "ACCESS_POINT"}]:
        reachable = _reachable(primary.id, edges, omitted_node=device.id)
        affected = sorted((all_ids - reachable) - {device.id})
        kind = "ACCESS_POINT" if device.role == "ACCESS_POINT" else "GATEWAY" if device.role == "GATEWAY" else "SWITCH"
        result.append(
            FailureScenario(
                id=f"failure-device-{device.id}",
                targetId=device.id,
                targetKind=kind,
                title=f"Loss of a collected {device.role.lower().replace('_', ' ')}",
                confidence="HIGH" if device.observed else "UNKNOWN",
                consequences=[f"{len(affected)} additional graph nodes become unreachable from the primary collected device."],
                affectedIds=affected,
                controlImpact="Control-path impact follows only the observed graph; out-of-band access is unknown.",
                evidenceIds=device.evidence_ids,
            )
        )
    for edge in [item for item in edges if item.kind == "PORT_CHANNEL_MEMBER"]:
        result.append(
            FailureScenario(
                id=f"failure-member-{edge.id}",
                targetId=edge.from_node_id,
                targetKind="PORT_CHANNEL_MEMBER",
                title=f"Loss of port-channel member {edge.from_node_id.rsplit(':', 1)[-1]}",
                confidence="CONFIRMED",
                consequences=["Aggregate capacity falls; forwarding continuity depends on the remaining bundled members."],
                affectedIds=[edge.from_node_id],
                controlImpact="No control-path loss is claimed unless the physical graph proves this is the only member/path.",
                evidenceIds=edge.evidence_ids,
            )
        )
    for interface in [item for item in interfaces if item.poe_watts and item.poe_watts > 0]:
        result.append(
            FailureScenario(
                id=f"failure-poe-{_token(interface.id)}",
                targetId=interface.id,
                targetKind="POE",
                title=f"Loss of PoE on {interface.name}",
                confidence="CONFIRMED",
                consequences=["The currently powered attachment on this interface loses power."],
                affectedIds=[interface.id],
                controlImpact="SwitchOps remains available unless its own observed path depends on the powered attachment.",
                evidenceIds=interface.evidence_ids,
            )
        )
    return result


STATE_ORDER = {"PROVEN": 0, "INFERRED": 1, "EXPECTED": 2, "AMBIGUOUS": 3, "UNKNOWN": 4}


def _paths(devices: list[LabDevice], edges: list[LabEdge]) -> list[LabPath]:
    if not devices:
        return []
    primary = next((item for item in devices if item.primary), devices[0])
    device_map = devices_by_id(devices)
    adjacency: dict[str, list[tuple[str, LabEdge]]] = defaultdict(list)
    for edge in edges:
        if edge.kind == "PORT_CHANNEL_MEMBER":
            continue
        adjacency[edge.from_node_id].append((edge.to_node_id, edge))
        adjacency[edge.to_node_id].append((edge.from_node_id, edge))
    paths: list[LabPath] = []
    for target in devices:
        if target.id == primary.id:
            continue
        queue = deque([primary.id])
        previous: dict[str, tuple[str, LabEdge] | None] = {primary.id: None}
        while queue and target.id not in previous:
            current = queue.popleft()
            for neighbor, edge in adjacency[current]:
                if neighbor not in previous:
                    previous[neighbor] = (current, edge)
                    queue.append(neighbor)
        if target.id not in previous:
            paths.append(
                LabPath(
                    id=f"path-{_token(primary.id, target.id)}",
                    fromNodeId=primary.id,
                    toNodeId=target.id,
                    state="UNKNOWN",
                    summary="No evidence-backed path connects these nodes.",
                    hops=[PathHop(nodeId=primary.id, label=primary.label, state="PROVEN"), PathHop(nodeId=target.id, label=target.label, state="UNKNOWN")],
                )
            )
            continue
        chain: list[tuple[str, LabEdge | None]] = []
        cursor = target.id
        while cursor != primary.id:
            prior, edge = previous[cursor]  # type: ignore[misc]
            chain.append((cursor, edge))
            cursor = prior
        chain.append((primary.id, None))
        chain.reverse()
        states = [edge.state for _, edge in chain if edge is not None]
        path_state = max(states, key=lambda value: STATE_ORDER[value]) if states else "PROVEN"
        evidence_ids = [value for _, edge in chain if edge for value in edge.evidence_ids]
        hops = []
        for node_id, edge in chain:
            node = device_map.get(node_id)
            hops.append(
                PathHop(
                    nodeId=node_id,
                    label=node.label if node else node_id,
                    viaInterface=edge.to_interface if edge and edge.to_node_id == node_id else edge.from_interface if edge else None,
                    state=edge.state if edge else "PROVEN",
                    evidenceIds=edge.evidence_ids if edge else [],
                )
            )
        paths.append(
            LabPath(
                id=f"path-{_token(primary.id, target.id)}",
                fromNodeId=primary.id,
                toNodeId=target.id,
                state=path_state,
                summary=f"{len(hops) - 1} evidence-backed hops; weakest hop is {path_state}.",
                hops=hops,
                evidenceIds=sorted(set(evidence_ids)),
            )
        )
    return paths


def build_lab_assurance_state(
    observations: list[LabDeviceObservation],
    *,
    performance: list[PerformanceObservation] | None = None,
) -> LabAssuranceState:
    now = datetime.now(timezone.utc)
    if not observations:
        return LabAssuranceState(
            generatedAt=now,
            collectionState="NOT_COLLECTED",
            summary=LabAssuranceSummary(
                observedDevices=0,
                physicalEdges=0,
                logicalNetworks=0,
                criticalFindings=0,
                warningFindings=0,
                unknownFindings=0,
                evidenceGaps=0,
            ),
            performance=performance or [],
            limitations=["Run a Lab Assurance refresh after configuring at least one IOS/IOS-XE device."],
        )
    devices, interfaces, edges, logical, capabilities, evidence, parts = _graph_from_observations(observations)
    findings = _findings(devices, interfaces, edges, logical, capabilities, evidence, observations, parts)
    failures = _failures(devices, interfaces, edges)
    paths = _paths(devices, edges)
    collected = [item for item in devices if item.collection_state in {"CURRENT", "PARTIAL", "FAILED"}]
    collection_state = "PARTIAL" if any(item.collection_state != "CURRENT" for item in collected) else "CURRENT"
    limitations = [
        "VLAN separation is not reported as isolation unless policy evidence proves enforcement.",
        "MAC and ARP observations express reachability, not guaranteed direct physical cabling.",
        "Failure consequences follow the observed graph; unobserved backup paths remain UNKNOWN.",
        "Advanced fabrics and routing features remain UNKNOWN or UNSUPPORTED when no positive read-only evidence exists.",
    ]
    return LabAssuranceState(
        generatedAt=now,
        collectionState=collection_state,
        summary=LabAssuranceSummary(
            observedDevices=len(collected),
            physicalEdges=sum(1 for item in edges if item.kind == "PHYSICAL"),
            logicalNetworks=len(logical),
            criticalFindings=sum(1 for item in findings if item.severity == "CRITICAL"),
            warningFindings=sum(1 for item in findings if item.severity == "WARNING"),
            unknownFindings=sum(1 for item in findings if item.severity == "UNKNOWN"),
            evidenceGaps=sum(1 for item in capabilities if item.state == "UNKNOWN"),
        ),
        devices=devices,
        interfaces=interfaces,
        edges=edges,
        logicalNetworks=logical,
        capabilities=capabilities,
        findings=findings,
        failures=failures,
        paths=paths,
        performance=performance or [],
        evidence=evidence,
        limitations=limitations,
    )
