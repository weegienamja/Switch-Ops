"""The exporter turns a real incident into a fixture that keeps no real data.

These tests pin the two properties that matter: nothing identifying survives,
and everything the diagnosis depends on does. A transform that failed the first
would leak; one that failed the second would produce fixtures that replay to a
different conclusion than the incident they came from.
"""
from __future__ import annotations

import ipaddress
import json

import pytest

from backend.resilience_lab.exporter import (
    PrivacyBudgetExceeded,
    PrivacyTransform,
    build_scenario,
    export,
)
from backend.resilience_lab.models import ResilienceScenario
from backend.resilience_lab.runner import ResilienceScenarioRunner


DOCUMENTATION = tuple(
    ipaddress.ip_network(value)
    for value in ("192.0.2.0/24", "198.51.100.0/24", "203.0.113.0/24")
)


def _incident() -> dict:
    """A response shaped like the DHCP-move incident.

    Deliberately not the operator's own addressing: the transform must be
    proven against realistic RFC1918 input without this repository retaining
    any real network's addresses.
    """
    return {
        "current": {
            "observedAt": "2026-08-26T16:54:35.357577Z",
            "supported": True,
            "adapterId": "adapter-a1b2c3d4e5f60718",
            "adapterName": "Ethernet",
            "interfaceIndex": 16,
            "interfaceMetric": 25,
            "adapterState": "Up",
            "sourceIp": "10.17.42.5",
            "prefixLength": 24,
            "connectedPrefix": "10.17.42.0/24",
            "targetOnConnectedPrefix": False,
            "dhcpEnabled": True,
            "dhcpStaticCoexistence": False,
            "dhcpServer": "10.17.42.1",
            "dhcpLeaseObtained": "2026-08-26T11:01:01.795673Z",
            "defaultGateway": "10.17.42.1",
            "route": {
                "destinationPrefix": "0.0.0.0/0",
                "nextHop": "10.17.42.1",
                "kind": "default",
                "routeMetric": 0,
                "protocol": "NetMgmt",
            },
            "windowsConnectivity": "Internet",
            "tcp22": "timed_out",
            "icmpReachable": False,
        },
        "lastKnownGood": {
            "observedAt": "2026-08-25T10:38:13.204327Z",
            "lastDeviceSuccessAt": "2026-08-25T10:38:13.204327Z",
            "adapterId": "adapter-a1b2c3d4e5f60718",
            "adapterName": "Ethernet",
            "sourceIp": "172.20.9.95",
            "prefixLength": 24,
            "connectedPrefix": "172.20.9.0/24",
            "managementPrefix": "172.20.9.0/24",
            "catalystGateway": "172.20.9.1",
            "catalystInterface": "Gi0/2",
            "sameAdapterAsCurrent": True,
            "freshness": "stale",
        },
        "diagnosis": {
            "conclusion": "HOST_NETWORK_CHANGED",
            "confidence": "MEDIUM",
            "headline": "Management path degraded",
            "summary": "The host left the last-known Catalyst management prefix.",
            "evidence": ["Windows selected Ethernet with source 10.17.42.5."],
            "missingEvidence": [],
        },
        "recoveryPlan": {"status": "BLOCKED", "blockers": []},
    }


def _addresses(node) -> list[str]:
    found: list[str] = []

    def walk(value) -> None:
        if isinstance(value, dict):
            for child in value.values():
                walk(child)
        elif isinstance(value, list):
            for child in value:
                walk(child)
        elif isinstance(value, str):
            for token in value.replace("/", " ").split():
                try:
                    found.append(str(ipaddress.ip_address(token)))
                except ValueError:
                    continue

    walk(node)
    return found


def test_no_real_address_survives_the_transform():
    scenario = export(_incident(), scenario_id="REAL_INCIDENT")
    rendered = json.dumps(scenario)
    for leaked in ("10.17.42.5", "172.20.9.95", "172.20.9.1", "10.17.42.1"):
        assert leaked not in rendered
    for address in _addresses(scenario):
        parsed = ipaddress.ip_address(address)
        if parsed.is_unspecified:
            continue
        assert any(parsed in network for network in DOCUMENTATION), address


def test_local_adapter_identifier_is_replaced_but_stays_consistent():
    scenario = export(_incident(), scenario_id="REAL_INCIDENT")
    rendered = json.dumps(scenario)
    assert "adapter-a1b2c3d4e5f60718" not in rendered
    # The same real adapter in both phases must remain the same synthetic
    # adapter: the diagnosis depends on it being one adapter, not two.
    adapters = {
        phase["evidence"]["management"].get("adapterId")
        for phase in scenario["phases"]
    }
    assert adapters == {"adapter-0001"}


def test_subnet_relationships_are_preserved():
    scenario = export(_incident(), scenario_id="REAL_INCIDENT")
    healthy, incident = (phase["evidence"]["management"] for phase in scenario["phases"])
    healthy_net = ipaddress.ip_network(healthy["connectedPrefix"])
    incident_net = ipaddress.ip_network(incident["connectedPrefix"])
    # Two different real prefixes must remain two different synthetic prefixes.
    assert healthy_net != incident_net
    # The host was on-link historically and is not on-link now.
    assert ipaddress.ip_address(healthy["sourceIp"]) in healthy_net
    assert ipaddress.ip_address(incident["sourceIp"]) in incident_net
    assert ipaddress.ip_address(incident["sourceIp"]) not in healthy_net
    # The gateway stays inside the subnet it serves.
    assert ipaddress.ip_address(incident["defaultGateway"]) in incident_net


def test_host_position_within_the_subnet_is_preserved():
    scenario = export(_incident(), scenario_id="REAL_INCIDENT")
    healthy, incident = (phase["evidence"]["management"] for phase in scenario["phases"])
    assert healthy["sourceIp"].endswith(".95")
    assert incident["sourceIp"].endswith(".5")


def test_timing_deltas_are_preserved_while_wall_clock_is_not():
    incident = _incident()
    scenario = build_scenario(incident, scenario_id="REAL_INCIDENT")
    from datetime import datetime

    def parse(value: str) -> datetime:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))

    real_gap = parse(incident["current"]["observedAt"]) - parse(
        incident["lastKnownGood"]["observedAt"]
    )
    exported_gap = parse(scenario["phases"][1]["at"]) - parse(scenario["phases"][0]["at"])
    assert exported_gap == real_gap
    assert "2026-08-26" not in json.dumps(scenario)


def test_exported_scenario_replays_to_the_original_diagnosis():
    scenario = export(_incident(), scenario_id="REAL_INCIDENT")
    result = ResilienceScenarioRunner().run(ResilienceScenario.model_validate(scenario))
    assert result.status == "PASS"
    assert result.phases[0].actual.management_diagnosis == "MANAGEMENT_PATH_HEALTHY"
    assert result.phases[-1].actual.management_diagnosis == "HOST_NETWORK_CHANGED"
    assert all(phase.actual.writes_performed == 0 for phase in result.phases)


def test_credential_shaped_fields_are_dropped():
    incident = _incident()
    incident["current"]["apiKey"] = "not-a-real-key"
    incident["current"]["password"] = "not-a-real-password"
    scenario = export(incident, scenario_id="REAL_INCIDENT")
    rendered = json.dumps(scenario)
    assert "not-a-real-key" not in rendered
    assert "not-a-real-password" not in rendered


def test_free_text_evidence_is_scrubbed():
    transform = PrivacyTransform()
    scrubbed = transform.text("Windows selected Ethernet with source 10.17.42.5.")
    assert "10.17.42.5" not in scrubbed
    assert "Ethernet" in scrubbed


def test_link_local_is_preserved_because_it_identifies_nothing():
    transform = PrivacyTransform()
    assert transform.address("169.254.10.20") == "169.254.10.20"


def test_too_many_distinct_networks_is_refused_rather_than_collapsed():
    transform = PrivacyTransform()
    for octet in (10, 20, 30):
        transform.address(f"192.168.{octet}.5")
    with pytest.raises(PrivacyBudgetExceeded):
        # Collapsing a fourth prefix onto a reused synthetic prefix would
        # silently invent a same-subnet relationship that never existed.
        transform.address("192.168.40.5")


def test_export_refuses_evidence_without_a_current_observation():
    with pytest.raises(ValueError):
        export({"diagnosis": {}}, scenario_id="BROKEN")


# --- topology / attachment export ------------------------------------------

REAL_MAC = "a4:bb:6d:11:22:33"


def _dashboard(port: str | None, *, generated: str, hostname: str) -> dict:
    """A dashboard response shaped like a real one, with real-looking values."""
    entries = []
    if port:
        entries.append({"vlan": "10", "mac": REAL_MAC, "type": "DYNAMIC", "port": port})
    # Another endpoint that belongs to somebody else and must not be exported.
    entries.append(
        {"vlan": "10", "mac": "b8:27:eb:99:88:77", "type": "DYNAMIC", "port": "Gi0/9"}
    )
    return {
        "summary": {"hostname": hostname},
        "topology": {
            "generatedAt": generated,
            "rootDeviceId": "switch-root",
            "devices": [],
            "interfaces": [],
            "links": [],
        },
        "interfaces": {
            "interfaces": [
                {
                    "port": "Gi0/2",
                    "status": "connected" if port == "Gi0/2" else "notconnect",
                    "vlan": "10",
                    "name": "Jamie desk - room 14",
                },
                {
                    "port": "Gi0/5",
                    "status": "connected" if port == "Gi0/5" else "notconnect",
                    "vlan": "10",
                    "name": "",
                },
                {"port": "Gi0/9", "status": "connected", "vlan": "10", "name": ""},
            ]
        },
        "macTable": {"entries": entries},
    }


def _move_sequence() -> list[dict]:
    return [
        _dashboard("Gi0/2", generated="2026-08-27T09:00:00Z", hostname="SITE-A-CLOSET2-SW1"),
        _dashboard(None, generated="2026-08-27T09:01:00Z", hostname="SITE-A-CLOSET2-SW1"),
        _dashboard("Gi0/5", generated="2026-08-27T09:02:00Z", hostname="SITE-A-CLOSET2-SW1"),
    ]


def _exported_move() -> dict:
    from backend.resilience_lab.exporter import export_topology

    return export_topology(
        _move_sequence(), scenario_id="REAL_PORT_MOVE", endpoint_mac=REAL_MAC
    )


def test_topology_export_preserves_the_attachment_relationship():
    scenario = _exported_move()
    assert [phase["id"] for phase in scenario["phases"]] == [
        "observation-1",
        "observation-2",
        "observation-3",
    ]
    first, absent, moved = scenario["phases"]
    assert first["evidence"]["topology"]["macs"][0]["port"] == "Gi0/2"
    assert absent["evidence"]["topology"]["macs"] == []
    assert moved["evidence"]["topology"]["macs"][0]["port"] == "Gi0/5"
    assert moved["expected"]["topologyTransition"] == "ENDPOINT_MOVED"
    assert moved["expected"]["previousAttachment"] == "Gi0/2"
    assert moved["expected"]["currentAttachment"] == "Gi0/5"
    assert moved["expected"]["identityRetained"] is True


def test_topology_export_uses_one_stable_synthetic_identity():
    scenario = _exported_move()
    macs = {
        entry["mac"]
        for phase in scenario["phases"]
        for entry in phase["evidence"]["topology"]["macs"]
    }
    assert len(macs) == 1
    synthetic = macs.pop()
    assert synthetic.startswith("0000.5e00.53")
    # The adapter must carry the same pseudonym, or correlation breaks.
    for phase in scenario["phases"]:
        assert phase["evidence"]["topology"]["adapters"][0]["mac"] == synthetic


def test_topology_export_retains_no_real_mac_hostname_or_description():
    rendered = json.dumps(_exported_move())
    for leaked in (
        REAL_MAC,
        REAL_MAC.replace(":", ""),
        "a4bb6d",
        "SITE-A-CLOSET2-SW1",
        "Jamie",
        "room 14",
    ):
        assert leaked.lower() not in rendered.lower()


def test_topology_export_omits_other_peoples_endpoints():
    rendered = json.dumps(_exported_move())
    assert "b8:27:eb" not in rendered
    assert "b827eb" not in rendered


def test_topology_export_preserves_relative_timing():
    from datetime import datetime

    scenario = _exported_move()

    def parse(value: str) -> datetime:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))

    at = [parse(phase["at"]) for phase in scenario["phases"]]
    assert (at[1] - at[0]).total_seconds() == 60
    assert (at[2] - at[1]).total_seconds() == 60
    assert "2026-08-27" not in json.dumps(scenario)


def test_topology_export_contains_no_paths_or_private_addresses():
    from backend.resilience_lab.catalog import _validate_fixture_privacy

    # Raises if any non-documentation address or non-documentation MAC survives.
    _validate_fixture_privacy(_exported_move(), location="exported")


def test_exported_port_move_replays_to_a_retained_identity():
    scenario = ResilienceScenario.model_validate(_exported_move())
    result = ResilienceScenarioRunner().run(scenario)
    assert result.status == "PASS", [
        (phase.phase_id, assertion.dimension, assertion.expectation, assertion.actual)
        for phase in result.phases
        for assertion in phase.assertions
        if not assertion.passed
    ]
    final = result.phases[-1]
    assert "ENDPOINT_MOVED" in final.actual.topology_transitions
    assert final.actual.identity_retained is True
    assert final.actual.current_attachment == "Gi0/5"
    assert final.actual.previous_attachment == "Gi0/2"
    assert final.actual.duplicate_entity_ids == 0
    assert all(phase.actual.writes_performed == 0 for phase in result.phases)


def test_topology_export_requires_at_least_one_observation():
    from backend.resilience_lab.exporter import export_topology

    with pytest.raises(ValueError):
        export_topology([], scenario_id="EMPTY", endpoint_mac=REAL_MAC)
