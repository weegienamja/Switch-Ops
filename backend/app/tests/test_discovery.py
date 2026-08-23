from backend.app.discovery import (
    LocalAdapter,
    correlate_local_endpoint,
    inspect_lldp,
    inspect_snmp_config,
)
from backend.app.models import ArpEntry, InterfaceStatus, MacTableEntry
from backend.app.parsers.lldp import parse_lldp, parse_lldp_detail, parse_lldp_summary
from backend.app.topology import build_topology


LLDP_DETAIL = """
------------------------------------------------
Local Intf: GigabitEthernet0/1
Chassis id: 0200.0000.000B
Port id: port-1
Port Description: Internet 1
System Name: TEST-GATEWAY-01-HQ
System Description:
Cisco Meraki TEST-GATEWAY-01 cloud managed appliance
Time remaining: 105 seconds
System Capabilities: B,R
Enabled Capabilities: B,R
Management Addresses:
    IP: 192.0.2.19
"""


def test_lldp_detail_normalises_direct_neighbor_evidence():
    neighbors = parse_lldp_detail(LLDP_DETAIL)
    assert len(neighbors) == 1
    assert neighbors[0].remote_name == "TEST-GATEWAY-01-HQ"
    assert neighbors[0].chassis_id == "0200.0000.000B"
    assert neighbors[0].local_interface == "Gi0/1"
    assert neighbors[0].remote_interface == "port-1"
    assert neighbors[0].ip == "192.0.2.19"
    assert neighbors[0].capabilities == ["B", "R"]
    assert "Meraki TEST-GATEWAY-01" in (neighbors[0].system_description or "")


def test_lldp_summary_is_a_tolerant_fallback():
    summary = """
Device ID           Local Intf     Hold-time  Capability      Port ID
TEST-GATEWAY-01-HQ           Gi0/1          120        B,R             port-1
Total entries displayed: 1
"""
    neighbors = parse_lldp_summary(summary)
    assert [(item.remote_name, item.local_interface) for item in neighbors] == [
        ("TEST-GATEWAY-01-HQ", "Gi0/1")
    ]
    assert parse_lldp("", summary) == neighbors


def test_lldp_disabled_and_unsupported_are_reported_honestly():
    disabled = inspect_lldp(
        running_config="hostname switch\n",
        summary_output="% LLDP is not enabled",
        detail_output="% LLDP is not enabled",
    )
    assert disabled.state == "disabled"
    assert disabled.supported is True
    assert disabled.neighbors == []

    unsupported = inspect_lldp(
        running_config="hostname switch\n",
        summary_output="% Invalid input detected at '^' marker.",
        detail_output="",
    )
    assert unsupported.state == "unsupported"
    assert unsupported.supported is False


def test_lldp_neighbor_becomes_direct_topology_evidence():
    topology = build_topology(
        hostname="lab-sw",
        model="WS-C3560",
        management_ip="192.0.2.190",
        interfaces=[InterfaceStatus(port="Gi0/1", status="connected", vlan="1")],
        mac_entries=[],
        poe_ports=[],
        lldp_neighbors=parse_lldp_detail(LLDP_DETAIL),
    )
    endpoint = next(device for device in topology.devices if device.type != "switch")
    assert endpoint.name == "TEST-GATEWAY-01-HQ"
    assert endpoint.identity_source == "lldp"
    assert endpoint.evidence_level == "direct"


def _interface() -> InterfaceStatus:
    return InterfaceStatus(port="Gi0/2", status="connected", vlan="1")


def _adapter(mac: str = "0000.5e00.530E") -> LocalAdapter:
    return LocalAdapter(
        name="Ethernet",
        ip="192.0.2.22",
        netmask="255.255.255.0",
        mac=mac,
    )


def _mac(mac: str = "0000.5e00.530E", port: str = "Gi0/2") -> MacTableEntry:
    return MacTableEntry(vlan="1", mac=mac, type="DYNAMIC", port=port)


def test_unique_local_adapter_correlation_confirms_the_pc_without_exposing_mac():
    result = correlate_local_endpoint(
        management_ip="192.0.2.190",
        adapters=[_adapter()],
        mac_entries=[_mac()],
        arp_entries=[ArpEntry(ip="192.0.2.22", mac="0000.5e00.530E", interface="Vlan1")],
        interfaces=[_interface()],
    )
    assert result.state == "confirmed"
    assert result.interface == "Gi0/2"
    assert result.label == "This SwitchOps PC"
    assert "d8bb" not in result.model_dump_json().lower()

    topology = build_topology(
        hostname="lab-sw",
        model="WS-C3560",
        management_ip="192.0.2.190",
        interfaces=[_interface()],
        mac_entries=[_mac()],
        poe_ports=[],
        local_endpoint=result,
    )
    endpoint = next(device for device in topology.devices if device.type != "switch")
    assert endpoint.name == "This SwitchOps PC"
    assert endpoint.identity_source == "local-host"


def test_randomised_or_multi_address_local_paths_stay_ambiguous():
    randomised = correlate_local_endpoint(
        management_ip="192.0.2.190",
        adapters=[_adapter("0200.0000.0008")],
        mac_entries=[_mac("0200.0000.0008")],
        arp_entries=[],
        interfaces=[_interface()],
    )
    assert randomised.state == "ambiguous"

    behind_other_devices = correlate_local_endpoint(
        management_ip="192.0.2.190",
        adapters=[_adapter()],
        mac_entries=[_mac(), _mac("0000.5e00.5303")],
        arp_entries=[],
        interfaces=[_interface()],
    )
    assert behind_other_devices.state == "ambiguous"


def test_snmp_inspection_returns_counts_but_never_secret_identifiers():
    result = inspect_snmp_config(
        """
snmp-server community privateReadName RO
snmp-server community privateWriteName RW 10
snmp-server group ops v3 priv
snmp-server user secretUser ops v3 auth sha hiddenAuth priv aes 128 hiddenPriv
snmp-server host 192.0.2.20 version 3 priv secretUser
"""
    )
    payload = result.model_dump_json()
    assert result.configured is True
    assert result.versions == ["v1/v2c", "v3"]
    assert result.read_only_communities == 1
    assert result.read_write_communities == 1
    assert result.v3_users == 1
    assert result.trap_hosts == 1
    for secret in ("privateReadName", "privateWriteName", "secretUser", "hiddenAuth", "hiddenPriv"):
        assert secret not in payload
