from datetime import datetime, timezone

from backend.app.models import CdpNeighbor, InterfaceStatus, MacTableEntry, PoePort
from backend.app.parsers.cdp import parse_cdp
from backend.app.topology import (
    build_topology,
    classify_device,
    classify_interface_role,
)


def _uplink_interface(**overrides):
    base = dict(
        port="Gi0/1",
        name="Uplink to Test Gateway",
        status="connected",
        vlan="1",
        duplex="a-full",
        speed="a-1000",
    )
    base.update(overrides)
    return InterfaceStatus(**base)


def _macs(port: str, *suffixes: str) -> list[MacTableEntry]:
    return [
        MacTableEntry(vlan="1", mac=f"a4b1.c1aa.bb{suffix}", type="DYNAMIC", port=port)
        for suffix in suffixes
    ]


def test_progressive_classification_uses_only_available_evidence():
    assert classify_device("")[0:4] == ("unknown", None, None, "unknown")
    assert classify_device("Office server")[0:4] == ("server", None, None, "category")
    assert classify_device("Meraki access point")[0:4] == (
        "access-point",
        "Cisco Meraki",
        None,
        "vendor",
    )
    assert classify_device("Meraki MR44 access point")[0:4] == (
        "access-point",
        "Cisco Meraki",
        "MR44",
        "model",
    )


def test_learned_mac_becomes_observed_visual_device():
    topology = build_topology(
        hostname="SYNTH-SW1",
        model="WS-C3560",
        management_ip="192.0.2.10",
        interfaces=[InterfaceStatus(
            port="Gi0/2",
            name="Main desktop",
            status="connected",
            vlan="1",
            duplex="a-full",
            speed="a-1000",
        )],
        mac_entries=[MacTableEntry(
            vlan="1", mac="0200.0000.0003", type="DYNAMIC", port="Gi0/2"
        )],
        poe_ports=[PoePort(interface="Gi0/2", oper="off")],
        observed_at=datetime(2026, 8, 22, tzinfo=timezone.utc),
    )

    endpoint = next(
        device for device in topology.devices if device.id != topology.root_device_id
    )
    assert endpoint.source == "observed"
    assert endpoint.online is True
    assert endpoint.evidence_level == "observed-on-port"
    # Presence is observed; identity is not. The description is held on the
    # expected facet and never becomes the node's name or type.
    assert endpoint.name == "Unidentified device"
    assert endpoint.type == "unknown"
    assert endpoint.identity_source == "none"
    assert endpoint.expected_name == "Main desktop"
    assert endpoint.expected_type == "desktop"
    assert endpoint.mac == "0200.0000.0003"
    assert endpoint.learned_mac_count == 1
    assert topology.links[0].status == "up"
    assert topology.links[0].confidence == "high"
    assert topology.links[0].evidence_level == "observed-on-port"


def test_description_without_mac_is_expected_not_discovered():
    topology = build_topology(
        hostname="SYNTH-SW1",
        model="WS-C3560",
        management_ip="192.0.2.10",
        interfaces=[InterfaceStatus(
            port="Gi0/4",
            name="SYNTH-MR44-01 AP",
            status="notconnect",
            vlan="1",
            duplex="auto",
            speed="auto",
        )],
        mac_entries=[],
        poe_ports=[PoePort(interface="Gi0/4", oper="off")],
    )

    expected = next(device for device in topology.devices if device.type == "access-point")
    assert expected.source == "expected"
    assert expected.online is False
    assert expected.evidence_level == "expected"
    assert expected.identity_source == "interface-description"
    assert "description only" in expected.evidence[0]
    assert topology.links[0].status == "waiting"
    assert topology.links[0].evidence_level == "expected"


def test_spare_port_does_not_fabricate_expected_device():
    topology = build_topology(
        hostname="SYNTH-SW1",
        model="WS-C3560",
        management_ip="192.0.2.10",
        interfaces=[InterfaceStatus(port="Gi0/8", name="Spare Access Port", status="disabled")],
        mac_entries=[],
        poe_ports=[],
    )
    assert len(topology.devices) == 1
    assert topology.links == []


# --- uplink correlation regressions ---------------------------------------
#
# The v0.2 builder created one device per learned MAC and copied the interface
# description onto each, so an uplink learning five addresses rendered as
# "Uplink to Test Gateway 1..5" — five directly connected routers that do not
# exist. These tests pin the corrected behaviour.


def test_many_macs_behind_uplink_do_not_become_separate_routers():
    topology = build_topology(
        hostname="SWITCHOPS-TEST-SW1",
        model="WS-C3560CG-8PC-S",
        management_ip="192.0.2.10",
        interfaces=[_uplink_interface()],
        mac_entries=_macs("Gi0/1", "01", "02", "03", "04", "05"),
        poe_ports=[],
        observed_at=datetime(2026, 8, 22, tzinfo=timezone.utc),
    )

    endpoints = [d for d in topology.devices if d.id != topology.root_device_id]
    assert len(endpoints) == 1, "an uplink must never expand into one node per learned MAC"
    assert len(topology.links) == 1

    endpoint = endpoints[0]
    # No "Uplink to Test Gateway 1", "... 2" duplicates - and no borrowed name at
    # all: nothing announced itself, so nothing is identified.
    assert endpoint.name == "Unidentified device"
    assert endpoint.expected_name == "Uplink to Test Gateway"
    assert endpoint.type == "unknown"
    assert endpoint.learned_mac_count == 5
    # One of five addresses must not be attributed to the neighbour.
    assert endpoint.mac is None
    assert endpoint.evidence_level == "observed-on-port"
    # Nothing identified it, so no identity source is claimed.
    assert endpoint.identity_source == "none"
    assert endpoint.role == "uplink"
    assert topology.links[0].learned_mac_count == 5


def test_uplink_endpoint_names_are_unique_per_interface():
    """Two uplinks with the same description must not collide or duplicate."""
    topology = build_topology(
        hostname="SWITCHOPS-TEST-SW1",
        model="WS-C3560CG-8PC-S",
        management_ip="192.0.2.10",
        interfaces=[
            _uplink_interface(port="Gi0/1"),
            _uplink_interface(port="Gi0/9"),
        ],
        mac_entries=_macs("Gi0/1", "01", "02") + _macs("Gi0/9", "03", "04"),
        poe_ports=[],
    )
    endpoints = [d for d in topology.devices if d.id != topology.root_device_id]
    assert len(endpoints) == 2
    assert len({d.id for d in endpoints}) == 2
    assert {d.connected_interface for d in endpoints} == {"Gi0/1", "Gi0/9"}


def test_endpoint_identity_degrades_when_no_description_exists():
    topology = build_topology(
        hostname="SYNTH-SW1",
        model="WS-C3560",
        management_ip="192.0.2.10",
        interfaces=[InterfaceStatus(
            port="Gi0/5", name="", status="connected", vlan="1", speed="a-1000", duplex="a-full"
        )],
        mac_entries=_macs("Gi0/5", "aa"),
        poe_ports=[],
    )
    endpoint = next(d for d in topology.devices if d.id != topology.root_device_id)
    assert endpoint.type == "unknown"
    assert endpoint.visual_category == "unknown"
    assert endpoint.identity_source == "none"
    assert endpoint.confidence == "low"
    assert endpoint.name == "Unidentified device"
    assert endpoint.expected_name is None
    assert endpoint.evidence_level == "observed-on-port"


def test_stale_macs_on_a_down_port_do_not_create_an_observed_device():
    """A MAC that has not yet aged out is not proof of a present device."""
    topology = build_topology(
        hostname="SYNTH-SW1",
        model="WS-C3560",
        management_ip="192.0.2.10",
        interfaces=[InterfaceStatus(
            port="Gi0/3", name="Test Server", status="notconnect", vlan="1"
        )],
        mac_entries=_macs("Gi0/3", "77"),
        poe_ports=[],
    )
    endpoint = next(d for d in topology.devices if d.id != topology.root_device_id)
    assert endpoint.source == "expected"
    assert endpoint.evidence_level == "expected"
    assert endpoint.online is False


def test_cdp_neighbour_is_direct_evidence_and_outranks_the_description():
    topology = build_topology(
        hostname="SYNTH-SW1",
        model="WS-C3560CG-8PC-S",
        management_ip="192.0.2.10",
        interfaces=[InterfaceStatus(
            port="Gi0/4",
            name="SYNTH-MR44-01 AP",
            status="connected",
            vlan="1",
            duplex="a-full",
            speed="a-1000",
        )],
        mac_entries=_macs("Gi0/4", "10", "11", "12"),
        poe_ports=[PoePort(interface="Gi0/4", oper="on", powerWatts=13.4)],
        cdp_neighbors=[CdpNeighbor(
            remoteName="SYNTH-MR44-Lab",
            localInterface="Gi0/4",
            remoteInterface="wired0",
            platform="Meraki MR44",
            capabilities=["Trans-Bridge"],
        )],
    )
    endpoint = next(d for d in topology.devices if d.id != topology.root_device_id)
    assert endpoint.evidence_level == "direct"
    assert endpoint.identity_source == "cdp"
    assert endpoint.confidence == "high"
    assert endpoint.name == "SYNTH-MR44-Lab"
    assert endpoint.vendor == "Cisco Meraki"
    assert endpoint.model == "MR44"
    assert endpoint.type == "access-point"
    # Wireless clients behind the AP stay a count, never extra nodes.
    assert endpoint.learned_mac_count == 3
    assert len([d for d in topology.devices if d.id != topology.root_device_id]) == 1
    link = topology.links[0]
    assert link.evidence_level == "direct"
    assert link.to_interface == "wired0"
    assert link.poe is True


def test_interface_role_classification():
    assert classify_interface_role("Uplink to Test Gateway", "1") == "uplink"
    assert classify_interface_role("Test Workstation", "1") == "access"
    assert classify_interface_role("", "1") == "unknown"
    assert classify_interface_role("Spare Uplink", "1") == "unknown"
    assert classify_interface_role("Anything", "trunk") == "uplink"


def test_every_interface_yields_at_most_one_endpoint_node():
    """The invariant that prevents the duplicate-router class of bug."""
    interfaces = [
        _uplink_interface(port="Gi0/1"),
        InterfaceStatus(port="Gi0/2", name="Test Workstation", status="connected", vlan="1"),
        InterfaceStatus(port="Gi0/3", name="Test Server", status="notconnect", vlan="1"),
        InterfaceStatus(port="Gi0/4", name="SYNTH-MR44-01 AP", status="notconnect", vlan="1"),
        InterfaceStatus(port="Gi0/5", name="TV", status="notconnect", vlan="1"),
        InterfaceStatus(port="Gi0/6", name="Spare Access Port", status="disabled", vlan="1"),
    ]
    topology = build_topology(
        hostname="SWITCHOPS-TEST-SW1",
        model="WS-C3560CG-8PC-S",
        management_ip="192.0.2.10",
        interfaces=interfaces,
        mac_entries=_macs("Gi0/1", "01", "02", "03", "04") + _macs("Gi0/2", "20"),
        poe_ports=[],
    )
    per_interface: dict[str, int] = {}
    for device in topology.devices:
        if device.id == topology.root_device_id:
            continue
        per_interface[device.connected_interface or ""] = (
            per_interface.get(device.connected_interface or "", 0) + 1
        )
    assert per_interface == {"Gi0/1": 1, "Gi0/2": 1, "Gi0/3": 1, "Gi0/4": 1, "Gi0/5": 1}
    assert len(topology.links) == 5
    # And the link count never exceeds one per interface either.
    assert len({link.from_interface for link in topology.links}) == len(topology.links)


def test_interface_carries_learned_count_for_the_ui():
    topology = build_topology(
        hostname="SYNTH-SW1",
        model="WS-C3560",
        management_ip="192.0.2.10",
        interfaces=[_uplink_interface()],
        mac_entries=_macs("Gi0/1", "01", "02", "03"),
        poe_ports=[],
    )
    interface = next(i for i in topology.interfaces if i.port == "Gi0/1")
    assert interface.learned_mac_count == 3
    assert interface.role == "uplink"


def test_cpu_and_all_vlan_mac_rows_are_ignored():
    topology = build_topology(
        hostname="SYNTH-SW1",
        model="WS-C3560",
        management_ip="192.0.2.10",
        interfaces=[InterfaceStatus(port="Gi0/2", name="Test Workstation", status="connected", vlan="1")],
        mac_entries=[
            MacTableEntry(vlan="All", mac="0200.0000.0005", type="STATIC", port="CPU"),
            MacTableEntry(vlan="1", mac="0200.0000.000C", type="DYNAMIC", port="Gi0/2"),
        ],
        poe_ports=[],
    )
    endpoint = next(d for d in topology.devices if d.id != topology.root_device_id)
    assert endpoint.learned_mac_count == 1


# --- CDP parsing ----------------------------------------------------------

CDP_SAMPLE = """-------------------------
Device ID: SYNTH-MR44-Lab
Entry address(es):
  IP address: 192.0.2.44
Platform: Meraki MR44,  Capabilities: Trans-Bridge
Interface: GigabitEthernet0/4,  Port ID (outgoing port): wired0
Holdtime : 155 sec

-------------------------
Device ID: Core-SW2
Entry address(es):
  IP address: 192.0.2.2
Platform: cisco WS-C2960-8TC-L,  Capabilities: Switch IGMP
Interface: GigabitEthernet0/9,  Port ID (outgoing port): GigabitEthernet0/1
Holdtime : 132 sec

Total cdp entries displayed : 2
"""


def test_parse_cdp_extracts_neighbours_and_shortens_interfaces():
    neighbors = parse_cdp(CDP_SAMPLE)
    assert len(neighbors) == 2
    first = neighbors[0]
    assert first.remote_name == "SYNTH-MR44-Lab"
    assert first.local_interface == "Gi0/4"
    assert first.remote_interface == "wired0"
    assert first.platform == "Meraki MR44"
    assert first.capabilities == ["Trans-Bridge"]
    assert first.ip == "192.0.2.44"
    assert neighbors[1].local_interface == "Gi0/9"


def test_parse_cdp_tolerates_empty_and_noisy_output():
    assert parse_cdp("") == []
    assert parse_cdp("-------------------------\nTotal cdp entries displayed : 0\n") == []
    assert parse_cdp("unexpected banner text") == []


# --- synthetic topology shape ----------------------------------------------------
#
# Mirrors the interface table of the real WS-C3560CG-8PC-S so a future change
# cannot silently alter what that switch renders as. Addresses are from the
# documentation range; no real lab address is committed.

PHYSICAL_LAB_INTERFACES = [
    InterfaceStatus(port="Gi0/1", name="Uplink to Test Gateway", status="connected", vlan="1", duplex="a-full", speed="a-1000", protected=True),
    InterfaceStatus(port="Gi0/2", name="Test Workstation", status="connected", vlan="1", duplex="a-full", speed="a-1000", protected=True),
    InterfaceStatus(port="Gi0/3", name="Test Server", status="notconnect", vlan="1", duplex="auto", speed="auto"),
    InterfaceStatus(port="Gi0/4", name="SYNTH-MR44-01 AP", status="notconnect", vlan="1", duplex="auto", speed="auto"),
    InterfaceStatus(port="Gi0/5", name="TV", status="notconnect", vlan="1", duplex="auto", speed="auto"),
    InterfaceStatus(port="Gi0/6", name="Spare Access Port", status="disabled", vlan="1", duplex="auto", speed="auto"),
    InterfaceStatus(port="Gi0/7", name="Spare Access Port", status="disabled", vlan="1", duplex="auto", speed="auto"),
    InterfaceStatus(port="Gi0/8", name="Spare Access Port", status="disabled", vlan="1", duplex="auto", speed="auto"),
    InterfaceStatus(port="Gi0/9", name="Spare Uplink", status="disabled", vlan="1", duplex="auto", speed="auto"),
    InterfaceStatus(port="Gi0/10", name="Spare", status="disabled", vlan="1", duplex="auto", speed="auto"),
]


def _synthetic_topology_fixture(uplink_macs: int = 3):
    return build_topology(
        hostname="SWITCHOPS-TEST-SW1",
        model="WS-C3560CG-8PC-S",
        management_ip="192.0.2.10",
        interfaces=PHYSICAL_LAB_INTERFACES,
        mac_entries=_macs("Gi0/1", *[f"{index:02d}" for index in range(uplink_macs)])
        + _macs("Gi0/2", "20"),
        poe_ports=[PoePort(interface=f"Gi0/{index}", oper="off") for index in range(1, 9)],
        observed_at=datetime(2026, 8, 22, tzinfo=timezone.utc),
    )


def test_synthetic_fixture_renders_the_devices_that_actually_exist():
    topology = _synthetic_topology_fixture()
    rendered = {
        device.connected_interface: (device.name, device.type, device.source, device.evidence_level)
        for device in topology.devices
        if device.id != topology.root_device_id
    }
    assert rendered == {
        # Linked ports: presence observed, identity absent, description kept
        # as the expectation it is.
        "Gi0/1": ("Unidentified device", "unknown", "observed", "observed-on-port"),
        "Gi0/2": ("Unidentified device", "unknown", "observed", "observed-on-port"),
        # Dark ports: intent only, so the description is the label.
        "Gi0/3": ("Test Server", "server", "expected", "expected"),
        "Gi0/4": ("SYNTH-MR44-01 AP", "access-point", "expected", "expected"),
        "Gi0/5": ("TV", "tv-media", "expected", "expected"),
    }
    expectations = {
        device.connected_interface: device.expected_name
        for device in topology.devices
        if device.id != topology.root_device_id
    }
    assert expectations["Gi0/1"] == "Uplink to Test Gateway"
    assert expectations["Gi0/2"] == "Test Workstation"
    # Spare and disabled ports invent nothing, including "Spare Uplink".
    assert "Gi0/6" not in rendered and "Gi0/9" not in rendered and "Gi0/10" not in rendered


def test_synthetic_fixture_uplink_stays_singular_however_many_addresses_appear():
    for uplink_macs in (1, 3, 5, 40):
        topology = _synthetic_topology_fixture(uplink_macs)
        uplink = [
            device for device in topology.devices if device.connected_interface == "Gi0/1"
        ]
        assert len(uplink) == 1, f"{uplink_macs} addresses produced {len(uplink)} nodes"
        assert uplink[0].name == "Unidentified device"
        assert uplink[0].expected_name == "Uplink to Test Gateway"
        assert uplink[0].learned_mac_count == uplink_macs
        # The whole topology grows by nothing as addresses accumulate.
        assert len(topology.devices) == 6
        assert len(topology.links) == 5


def test_synthetic_fixture_marks_the_management_ports_protected():
    topology = _synthetic_topology_fixture()
    protected = {i.port for i in topology.interfaces if i.protected}
    assert protected == {"Gi0/1", "Gi0/2"}


def test_synthetic_fixture_expected_ap_is_ready_for_the_mr44_transition():
    """Before the TEST-AP is plugged in, Gi0/4 must read as waiting, not offline."""
    topology = _synthetic_topology_fixture()
    ap = next(d for d in topology.devices if d.connected_interface == "Gi0/4")
    assert ap.source == "expected"
    assert ap.online is False
    assert ap.model == "MR44" and ap.vendor == "Cisco Meraki"
    # Identity is described, not discovered — the AP has never spoken.
    assert ap.identity_source == "interface-description"
    assert ap.confidence == "medium"
    link = next(l for l in topology.links if l.from_interface == "Gi0/4")
    assert link.status == "waiting"
    assert link.learned_mac_count == 0
