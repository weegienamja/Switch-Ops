from __future__ import annotations

import json
from datetime import datetime, timezone
from types import SimpleNamespace

from backend.app.lab_assurance import build_lab_assurance_state
from backend.app.cli import _privacy_safe
from backend.app.lab_collector import LAB_COMMANDS, LabDeviceObservation
from backend.app.credential_store import KeyringCredentialVault
from backend.app.lab_device_store import LabDeviceStore
from backend.app.models import LabDeviceCreateRequest
from backend.app.parsers.config_parser import parse_running_config
from backend.app.parsers.lab_assurance import parse_interface_rates
from backend.app.performance_probes import run_bounded_probe


NOW = datetime(2026, 8, 24, 12, 0, tzinfo=timezone.utc)


class FakeKeyring:
    def __init__(self) -> None:
        self.values: dict[tuple[str, str], str] = {}

    def set_password(self, service: str, account: str, value: str) -> None:
        self.values[(service, account)] = value

    def get_password(self, service: str, account: str) -> str | None:
        return self.values.get((service, account))

    def delete_password(self, service: str, account: str) -> None:
        self.values.pop((service, account), None)


def test_secondary_device_registry_persists_only_opaque_id(tmp_path):
    keyring = FakeKeyring()
    registry = tmp_path / "lab-device-registry.json"
    store = LabDeviceStore(
        registry,
        credential_vault=KeyringCredentialVault(keyring=keyring),
    )
    request = LabDeviceCreateRequest(
        label="Synthetic distribution switch",
        host="192.0.2.50",
        username="synthetic-user",
        password="synthetic-password",
        enableSecret="synthetic-enable",
        deviceType="cisco_xe",
    )

    saved = store.add(request)

    persisted = registry.read_text(encoding="utf-8")
    assert saved.id.startswith("lab-")
    assert json.loads(persisted) == {"device_ids": [saved.id]}
    for private_value in (
        request.label,
        request.host,
        request.username,
        request.password,
        request.enable_secret,
    ):
        assert private_value not in persisted
    loaded = store.credentials(saved.id)
    assert loaded is not None
    assert loaded[1].switch_host == request.host
    assert store.remove(saved.id) is True
    assert store.credentials(saved.id) is None


def test_running_config_emits_only_explicit_assurance_policy():
    parsed = parse_running_config(
        """hostname SYNTH-SW1
ip routing
ip dhcp snooping
ip arp inspection vlan 10
interface GigabitEthernet1/0/1
 switchport mode trunk
 switchport trunk native vlan 99
 switchport trunk allowed vlan 10,20,99
 channel-group 1 mode active
 ip dhcp snooping trust
 ip arp inspection trust
!
interface GigabitEthernet1/0/2
 switchport mode access
 switchport access vlan 10
 spanning-tree portfast
 spanning-tree bpduguard enable
 switchport port-security
!
"""
    )

    trunk = parsed["interfaces"]["GigabitEthernet1/0/1"]
    access = parsed["interfaces"]["GigabitEthernet1/0/2"]
    assert trunk["mode"] == "TRUNK"
    assert trunk["native_vlan"] == "99"
    assert trunk["allowed_vlans"] == "10,20,99"
    assert trunk["dhcp_snooping_trust"] is True
    assert access["access_vlan"] == "10"
    assert access["bpdu_guard"] is True
    assert access["port_security"] is True
    assert parsed["features"]["ip_routing"] is True
    assert parsed["features"]["dhcp_snooping"] is True
    assert parsed["features"]["dai"] is True


def _observation(device_id: str, hostname: str, peer: str, native_vlan: int) -> LabDeviceObservation:
    local = "GigabitEthernet1/0/1"
    command_state = {symbol: "empty" for symbol in LAB_COMMANDS}
    outputs = {symbol: "" for symbol in LAB_COMMANDS}
    outputs.update(
        {
            "show_version": f"Cisco IOS XE Software, Version 17.9.4, RELEASE SOFTWARE\n{hostname} uptime is 1 day\nModel number : C9200L-24P-4G",
            "show_inventory": 'NAME: "Chassis"\nDESCR: "Synthetic chassis"\nPID: C9200L-24P-4G, VID: V01, SN: SYNTHETIC',
            "show_running_config": f"""hostname {hostname}
ip routing
no ip http server
no ip http secure-server
interface {local}
 description TO-{peer}
 switchport mode trunk
 switchport trunk native vlan {native_vlan}
 switchport trunk allowed vlan 10,20,99
!
interface GigabitEthernet1/0/2
 switchport mode access
 switchport access vlan 10
 spanning-tree portfast
!
interface Vlan10
 ip address 192.0.2.2 255.255.255.0
!
""",
            "show_interfaces_status": """Port      Name               Status       Vlan       Duplex  Speed Type
Gi1/0/1   TO-PEER            connected    trunk      a-full a-1000 10/100/1000BaseTX
Gi1/0/2   CLIENT             notconnect   10         auto   auto   10/100/1000BaseTX
""",
            "show_interfaces_switchport": f"""Name: Gi1/0/1
Switchport: Enabled
Administrative Mode: trunk
Operational Mode: trunk
Trunking Native Mode VLAN: {native_vlan}
Trunking VLANs Enabled: 10,20,99

Name: Gi1/0/2
Switchport: Enabled
Administrative Mode: static access
Operational Mode: static access
Access Mode VLAN: 10 (USERS)
""",
            "show_vlan_brief": "10   USERS                            active    Gi1/0/2\n20   SERVERS                          active\n99   NATIVE                           active",
            "show_spanning_tree": """VLAN0010
  Spanning tree enabled protocol rstp
  Root ID    Priority    32778
             Address     0011.2233.4455
             This bridge is the root
Interface           Role Sts Cost      Prio.Nbr Type
Gi1/0/1             Desg FWD 4         128.1    P2p
""",
            "show_cdp_neighbors_detail": f"""-------------------------
Device ID: {peer}
Entry address(es):
  IP address: 192.0.2.3
Platform: cisco C9200L-24P-4G,  Capabilities: Switch IGMP
Interface: {local},  Port ID (outgoing port): {local}
""",
        }
    )
    for symbol, output in outputs.items():
        if output:
            command_state[symbol] = "observed"
    command_state["show_bgp_ipv4_unicast_summary"] = "unsupported"
    return LabDeviceObservation(
        device_id=device_id,
        configured_label=hostname,
        primary=device_id == "device-a",
        observed_at=NOW,
        outputs=outputs,
        command_state=command_state,
    )


def test_multi_device_graph_requires_exact_identity_and_preserves_evidence_states():
    state = build_lab_assurance_state(
        [
            _observation("device-a", "SYNTH-SW1", "SYNTH-SW2", 99),
            _observation("device-b", "SYNTH-SW2", "SYNTH-SW1", 20),
        ]
    )

    collected = [item for item in state.devices if item.collection_state == "CURRENT"]
    physical = [item for item in state.edges if item.kind == "PHYSICAL"]
    assert {item.label for item in collected} == {"SYNTH-SW1", "SYNTH-SW2"}
    assert len(physical) == 1
    assert physical[0].reciprocal is True
    assert physical[0].confidence == "CONFIRMED"
    assert physical[0].state == "PROVEN"
    assert any(item.title == "Reciprocal trunk native VLAN mismatch" for item in state.findings)
    assert all(network.isolation_state == "POLICY_UNKNOWN" for network in state.logical_networks)
    bgp = [item for item in state.capabilities if item.name == "BGP IPv4 unicast"]
    assert bgp and all(item.state == "UNSUPPORTED" for item in bgp)
    assert state.paths and any(path.state == "PROVEN" for path in state.paths)
    assert not hasattr(state.summary, "score")


def test_mac_learning_is_never_a_proven_physical_edge():
    observation = _observation("device-a", "SYNTH-SW1", "UNOBSERVED-PEER", 99)
    observation.outputs["show_mac_address_table"] = "  10    0200.0000.0001    DYNAMIC     Gi1/0/2"
    observation.command_state["show_mac_address_table"] = "observed"
    state = build_lab_assurance_state([observation])

    mac_edges = [item for item in state.edges if item.kind == "L2_MEMBERSHIP"]
    assert len(mac_edges) == 1
    assert mac_edges[0].state == "INFERRED"
    assert "not direct physical cabling" in next(
        item.detail for item in state.devices if item.id == mac_edges[0].to_node_id
    )


def test_cisco_identity_alone_never_grants_capabilities():
    observation = LabDeviceObservation(
        device_id="device-a",
        configured_label="SYNTH-SW1",
        primary=True,
        observed_at=NOW,
        outputs={symbol: "" for symbol in LAB_COMMANDS},
        command_state={symbol: "empty" for symbol in LAB_COMMANDS},
    )
    observation.outputs["show_version"] = "Cisco IOS XE Software, Version 17.9.4, RELEASE SOFTWARE\nSYNTH-SW1 uptime is 1 day"
    observation.command_state["show_version"] = "observed"

    state = build_lab_assurance_state([observation])

    assert state.capabilities
    assert all(item.state == "UNKNOWN" for item in state.capabilities)


def test_interface_rates_include_utilization_inputs_and_drops():
    rates = parse_interface_rates(
        """GigabitEthernet1/0/1 is up, line protocol is up
  Input queue: 0/75/12/0 (size/max/drops/flushes); Total output drops: 7
  5 minute input rate 800000000 bits/sec, 100 packets/sec
  5 minute output rate 100000000 bits/sec, 50 packets/sec
"""
    )
    assert rates["GigabitEthernet1/0/1"] == {
        "input_drops": 12,
        "output_drops": 7,
        "input_bps": 800000000,
        "output_bps": 100000000,
    }


def test_active_probe_uses_fixed_argument_arrays_and_redacts_target(monkeypatch):
    calls: list[tuple[list[str], dict]] = []

    def fake_run(args, **kwargs):
        calls.append((args, kwargs))
        if args[0].lower().startswith("ping"):
            return SimpleNamespace(
                stdout="Reply from 192.0.2.1: bytes=32 time=10ms TTL=64\nPackets: Sent = 2, Received = 2, Lost = 0 (0% loss)",
                stderr="",
            )
        return SimpleNamespace(
            stdout="  1    1 ms    1 ms    1 ms  192.0.2.1\n  2    2 ms    2 ms    2 ms  198.51.100.1",
            stderr="",
        )

    monkeypatch.setattr("backend.app.performance_probes.subprocess.run", fake_run)
    observation, signature = run_bounded_probe("example.test", label="Synthetic target", count=2)

    assert len(calls) == 2
    assert all(isinstance(args, list) for args, _ in calls)
    assert all(kwargs["shell"] is False for _, kwargs in calls)
    assert observation.state == "HEALTHY"
    assert observation.received == 2
    assert observation.latency_avg_ms == 10
    assert observation.target_token != "example.test"
    assert "example.test" not in observation.model_dump_json()
    assert signature is not None


def test_machine_report_replaces_local_labels_with_opaque_references():
    protected = _privacy_safe(
        {
            "devices": [{"id": "device-opaque", "label": "PRIVATE-SWITCH"}],
            "hops": [{"nodeId": "node-opaque", "label": "PRIVATE-GATEWAY"}],
            "probe": {"targetToken": "target-opaque", "targetLabel": "Private server"},
        }
    )
    rendered = json.dumps(protected)
    assert "PRIVATE-SWITCH" not in rendered
    assert "PRIVATE-GATEWAY" not in rendered
    assert "Private server" not in rendered
    assert "protected:device-opaque" in rendered
