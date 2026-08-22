from datetime import datetime, timezone

from backend.app.models import InterfaceStatus, MacTableEntry, PoePort
from backend.app.topology import build_topology, classify_device


def test_progressive_classification_uses_only_available_evidence():
    assert classify_device("")[0:4] == ("unknown", None, None, "unknown")
    assert classify_device("Office server")[0:4] == ("server", None, None, "category")
    assert classify_device("Meraki access point")[0:4] == (
        "access-point",
        "Cisco Meraki",
        None,
        "vendor",
    )
    assert classify_device("TEST-AP-01 AP")[0:4] == (
        "access-point",
        "Cisco Meraki",
        "TEST-AP",
        "model",
    )


def test_learned_mac_becomes_observed_visual_device():
    topology = build_topology(
        hostname="Lab-SW1",
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

    endpoint = next(device for device in topology.devices if device.type == "desktop")
    assert endpoint.source == "observed"
    assert endpoint.online is True
    assert topology.links[0].status == "up"
    assert topology.links[0].confidence == "high"


def test_description_without_mac_is_expected_not_discovered():
    topology = build_topology(
        hostname="Lab-SW1",
        model="WS-C3560",
        management_ip="192.0.2.10",
        interfaces=[InterfaceStatus(
            port="Gi0/4",
            name="TEST-AP-01 AP",
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
    assert "description only" in expected.evidence[0]
    assert topology.links[0].status == "waiting"


def test_spare_port_does_not_fabricate_expected_device():
    topology = build_topology(
        hostname="Lab-SW1",
        model="WS-C3560",
        management_ip="192.0.2.10",
        interfaces=[InterfaceStatus(port="Gi0/8", name="Spare Access Port", status="disabled")],
        mac_entries=[],
        poe_ports=[],
    )
    assert len(topology.devices) == 1
    assert topology.links == []
