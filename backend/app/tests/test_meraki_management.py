from __future__ import annotations

from datetime import datetime, timedelta, timezone
import ipaddress
import json
import sqlite3

from app.management_path import (
    LastKnownManagementPath,
    ManagementPathDiagnosis,
    ManagementPathObservation,
    ManagementRoute,
    apply_meraki_context,
)
from app.meraki_management import (
    MerakiManagementEvidence,
    MerakiPortEvidence,
    normalize_lans,
    normalize_ports,
)
from app.unified_store import UnifiedLabStore


NOW = datetime(2026, 8, 26, 9, 0, tzinfo=timezone.utc)
TARGET = "198.18.10.10"


def evidence(*, subnets: list[str], freshness: str = "current", state: str = "healthy"):
    return MerakiManagementEvidence(
        state=state,
        checkedAt=NOW,
        observedAt=NOW - (timedelta(hours=8) if freshness == "stale" else timedelta()),
        freshness=freshness,
        complete=state == "healthy",
        detail="Synthetic normalized current configuration.",
        vlansEnabled=True,
        lans=[
            {
                "vlanId": str(index + 10),
                "subnet": subnet,
                "applianceIp": str(next(ipaddress.ip_network(subnet).hosts())),
                "dhcpMode": "server",
            }
            for index, subnet in enumerate(subnets)
        ],
    )


def current_path() -> ManagementPathObservation:
    return ManagementPathObservation(
        observedAt=NOW,
        adapterId="adapter-synthetic",
        adapterName="Synthetic Ethernet",
        interfaceIndex=12,
        sourceIp="198.18.20.5",
        prefixLength=24,
        connectedPrefix="198.18.20.0/24",
        targetOnConnectedPrefix=False,
        dhcpEnabled=True,
        defaultGateway="198.18.20.1",
        route=ManagementRoute(
            destinationPrefix="0.0.0.0/0",
            nextHop="198.18.20.1",
            kind="default",
        ),
        tcp22="timed_out",
    )


def last_path() -> LastKnownManagementPath:
    return LastKnownManagementPath(
        observedAt=NOW - timedelta(minutes=10),
        sourceIp="198.18.10.95",
        connectedPrefix="198.18.10.0/24",
        sameAdapterAsCurrent=True,
        provenance=["synthetic-history"],
    )


def host_changed() -> ManagementPathDiagnosis:
    return ManagementPathDiagnosis(
        conclusion="HOST_NETWORK_CHANGED",
        confidence="HIGH",
        headline="Management path degraded",
        summary="Synthetic host network transition.",
        evidence=["Windows currently selects the default route."],
    )


def test_lan_normalization_covers_server_relay_disabled_and_unknown_dhcp() -> None:
    raw = [
        {
            "id": "10",
            "subnet": "198.18.10.7/24",
            "applianceIp": "198.18.10.1",
            "dhcpHandling": "Run a DHCP server",
            "dhcpLeaseTime": "1 day",
            "reservedIpRanges": [{"start": "secret", "end": "secret"}],
            "fixedIpAssignments": {"private-mac": {"name": "private-name"}},
            "unknownSecret": "must-not-survive",
        },
        {
            "id": "20",
            "subnet": "198.18.20.0/24",
            "applianceIp": "198.18.20.1",
            "dhcpHandling": "Relay DHCP to another server",
            "dhcpRelayServerIps": ["203.0.113.10", "203.0.113.11"],
        },
        {
            "id": "30",
            "subnet": "198.18.30.0/24",
            "dhcpHandling": "Do not respond to DHCP requests",
        },
        {"id": "40", "subnet": "198.18.40.0/24", "dhcpHandling": "External"},
    ]

    normalized = normalize_lans(vlans_enabled=True, raw_lans=raw)

    assert [item.subnet for item in normalized] == [
        "198.18.10.0/24",
        "198.18.20.0/24",
        "198.18.30.0/24",
        "198.18.40.0/24",
    ]
    assert [item.dhcp_mode for item in normalized] == [
        "server",
        "relay",
        "disabled",
        "unknown",
    ]
    assert normalized[0].reserved_range_count == 1
    assert normalized[0].fixed_assignment_count == 1
    assert normalized[1].dhcp_relay_server_count == 2
    serialized = json.dumps([item.model_dump(by_alias=True) for item in normalized])
    for forbidden in ("private-mac", "private-name", "must-not-survive", "203.0.113.10"):
        assert forbidden not in serialized


def test_appliance_port_normalization_covers_access_trunk_native_and_allowed_vlans() -> None:
    normalized = normalize_ports(
        [
            {"number": 1, "enabled": True, "type": "access", "vlan": 10},
            {
                "number": 2,
                "enabled": True,
                "type": "trunk",
                "vlan": 20,
                "allowedVlans": "10,20-30,private,bad-1",
            },
            {
                "number": 3,
                "enabled": False,
                "type": "trunk",
                "vlan": 30,
                "allowedVlans": "all",
            },
        ]
    )

    assert normalized[0].mode == "access"
    assert normalized[0].access_vlan == "10"
    assert normalized[0].native_vlan is None
    assert normalized[1].mode == "trunk"
    assert normalized[1].native_vlan == "20"
    assert normalized[1].allowed_vlans == ["10", "20-30"]
    assert normalized[2].allowed_vlans == ["all"]


def test_current_client_and_management_lans_prevent_a_causal_address_claim() -> None:
    result = apply_meraki_context(
        target=TARGET,
        current=current_path(),
        last_known_good=last_path(),
        diagnosis=host_changed(),
        meraki=evidence(subnets=["198.18.20.0/24", "198.18.10.0/24"]),
    )

    assert result.conclusion == "DEVICE_OR_PATH_UNREACHABLE"
    assert result.confidence == "INDETERMINATE"
    assert "may therefore be intentional" in result.summary
    assert not any("restart" in item.casefold() for item in result.evidence)


def test_current_client_lan_and_absent_historical_lan_strengthen_context_not_causation() -> None:
    result = apply_meraki_context(
        target=TARGET,
        current=current_path(),
        last_known_good=last_path(),
        diagnosis=host_changed(),
        meraki=evidence(subnets=["198.18.20.0/24"]),
    )

    assert result.conclusion == "HOST_NETWORK_CHANGED"
    assert result.confidence == "HIGH"
    assert any("DHCP mode server" in item for item in result.evidence)
    assert any("does not prove" in item for item in result.missing_evidence)
    assert not any("caused" in item.casefold() for item in result.evidence)


def test_unavailable_or_stale_meraki_evidence_never_hides_windows_diagnosis() -> None:
    unavailable = MerakiManagementEvidence.unavailable(
        checked_at=NOW,
        state="not-configured",
        detail="Synthetic credentials unavailable.",
    )
    stale = evidence(subnets=["198.18.20.0/24", "198.18.10.0/24"], freshness="stale")

    for meraki_context in (unavailable, stale):
        result = apply_meraki_context(
            target=TARGET,
            current=current_path(),
            last_known_good=last_path(),
            diagnosis=host_changed(),
            meraki=meraki_context,
        )
        assert result.conclusion == "HOST_NETWORK_CHANGED"
        assert result.confidence == "HIGH"
        assert result.missing_evidence


def test_partial_meraki_response_does_not_treat_an_unreturned_lan_as_absent() -> None:
    partial = evidence(subnets=["198.18.20.0/24"], state="partial")
    partial.complete = False
    partial.failed_operations = ["appliance_vlans"]

    result = apply_meraki_context(
        target=TARGET,
        current=current_path(),
        last_known_good=last_path(),
        diagnosis=host_changed(),
        meraki=partial,
    )

    assert result.conclusion == "HOST_NETWORK_CHANGED"
    assert not any("does not currently report" in item for item in result.evidence)
    assert any("partial Meraki response" in item for item in result.missing_evidence)


def test_runtime_health_marks_old_dashboard_snapshot_stale() -> None:
    stored = evidence(subnets=["198.18.20.0/24"])
    stored.observed_at = NOW - timedelta(hours=7)

    runtime = stored.with_runtime_health(
        state="unavailable",
        checked_at=NOW,
        detail="Synthetic Dashboard outage.",
        complete=False,
        failed_operations=["appliance_vlans"],
        now=NOW,
    )

    assert runtime.state == "unavailable"
    assert runtime.freshness == "stale"
    assert runtime.failed_operations == ["appliance_vlans"]


def test_store_persists_only_the_compact_normalized_snapshot(tmp_path) -> None:
    path = tmp_path / "unified.sqlite"
    store = UnifiedLabStore(path)
    normalized = evidence(subnets=["198.18.20.0/24"])
    normalized.ports = [
        MerakiPortEvidence(
            portId="3",
            enabled=True,
            mode="trunk",
            nativeVlan="20",
            allowedVlans=["10", "20"],
            catalystFacing=True,
        )
    ]
    store.save_meraki_management_evidence(normalized)
    restored = store.load_meraki_management_evidence()
    store.close()

    assert restored is not None
    assert restored.lans[0].subnet == "198.18.20.0/24"
    with sqlite3.connect(path) as connection:
        payload = connection.execute(
            "SELECT evidence_json FROM meraki_management_snapshots"
        ).fetchone()[0]
    for forbidden in (
        "fixedIpAssignments",
        "reservedIpRanges",
        "managementAddress",
        "clientMac",
        "administrator",
    ):
        assert forbidden not in payload
