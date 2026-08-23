from datetime import datetime, timedelta, timezone
import sqlite3

from backend.app.discovery_evidence import (
    evidence_record,
    freshness_for,
    identity_confidence,
    oui_vendor_hint,
    stable_entity_id,
    vendor_conflict,
)
from backend.app.discovery_store import DiscoveryHistoryStore
from backend.app.intent_store import TopologyIntentStore
from backend.app.models import CdpNeighbor, EvidenceClaimSupport, InterfaceStatus, MacTableEntry
from backend.app.reconciliation import (
    CiscoIosEvidenceProvider,
    HistoryProvider,
    IntentProvider,
    reconcile,
)
from backend.app.topology import build_topology


NOW = datetime(2026, 8, 23, 12, 0, tzinfo=timezone.utc)


def interface(port: str = "Gi0/3", status: str = "connected", name: str = ""):
    return InterfaceStatus(
        port=port,
        name=name,
        status=status,
        vlan="1",
        speed="a-1000",
        duplex="a-full",
    )


def mac(port: str = "Gi0/3", value: str = "001b.7700.0001"):
    return MacTableEntry(vlan="1", mac=value, type="DYNAMIC", port=port)


def topology(*, interfaces, macs=(), at=NOW):
    return build_topology(
        hostname="SYNTH-DISCOVERY-SW1",
        model="WS-C3560CG-8PC-S",
        management_ip="192.0.2.10",
        interfaces=interfaces,
        mac_entries=macs,
        poe_ports=[],
        observed_at=at,
    )


def test_oui_enrichment_is_local_and_conservative():
    registered = oui_vendor_hint("00:1b:77:00:00:01")
    assert registered.status == "registered"
    assert registered.vendor == "Intel Corporate"

    assert oui_vendor_hint("02:00:00:00:00:01").status == "locally-administered"
    assert oui_vendor_hint("01:00:5e:00:00:01").status == "multicast"
    assert oui_vendor_hint("ff:ff:ff:ff:ff:ff").status == "broadcast"
    assert oui_vendor_hint("not-a-mac").status == "invalid"
    assert oui_vendor_hint("00:1b:77:00:00:01 trailing-data").status == "invalid"


def test_link_only_endpoint_separates_existence_from_identity():
    result = topology(interfaces=[interface()], macs=[])
    endpoint = next(item for item in result.devices if item.id != result.root_device_id)
    assert endpoint.name == "Unidentified endpoint"
    assert endpoint.existence_confidence == "medium"
    assert endpoint.identity_confidence == "unknown"
    assert endpoint.relationship == "attached-endpoint"
    assert endpoint.evidence_ids

    link_fact = next(item for item in result.evidence if item.evidence_type == "INTERFACE_LINK")
    assert link_fact.establishes.existence is True
    assert link_fact.establishes.identity is False


def test_stable_mac_identity_survives_a_port_move():
    first = topology(interfaces=[interface("Gi0/2")], macs=[mac("Gi0/2")])
    second = topology(interfaces=[interface("Gi0/7")], macs=[mac("Gi0/7")])
    first_id = next(item.id for item in first.devices if item.id != first.root_device_id)
    second_id = next(item.id for item in second.devices if item.id != second.root_device_id)
    assert first_id == second_id
    assert first_id == stable_entity_id("physical", "mac", "001b77000001")


def test_arp_and_oui_enrich_but_do_not_invent_a_name():
    from backend.app.models import ArpEntry

    result = build_topology(
        hostname="SYNTH-DISCOVERY-SW1",
        model="WS-C3560CG-8PC-S",
        management_ip="192.0.2.10",
        interfaces=[interface()],
        mac_entries=[mac()],
        arp_entries=[ArpEntry(ip="192.0.2.55", mac="001b.7700.0001", interface="Vlan1")],
        poe_ports=[],
        observed_at=NOW,
    )
    endpoint = next(item for item in result.devices if item.id != result.root_device_id)
    assert endpoint.name == "Unidentified device"
    assert endpoint.vendor == "Intel Corporate"
    assert endpoint.ip_addresses == ["192.0.2.55"]
    assert endpoint.identity_confidence == "medium"
    assert {item.evidence_type for item in result.evidence} >= {
        "INTERFACE_LINK", "MAC_LEARNED", "ARP_ENTRY", "OUI_VENDOR"
    }


def test_conflict_lowers_identity_confidence_in_central_rules():
    oui = evidence_record(
        evidence_type="OUI_VENDOR",
        evidence_class="inferred",
        source="mac-oui",
        device_id="switch-synthetic",
        interface="Gi0/3",
        entity_id="entity-synthetic",
        observed_value="Example Vendor A",
        summary="Synthetic OUI hint.",
        observed_at=NOW,
        strength="low",
        establishes=EvidenceClaimSupport(identity=True),
        provenance="synthetic test registry",
    )
    direct = evidence_record(
        evidence_type="LLDP_NEIGHBOR",
        evidence_class="observed",
        source="lldp",
        device_id="switch-synthetic",
        interface="Gi0/3",
        entity_id="entity-synthetic",
        observed_value="SYNTH-ENDPOINT-1",
        summary="Synthetic LLDP neighbour.",
        observed_at=NOW,
        strength="high",
        establishes=EvidenceClaimSupport(identity=True),
        provenance="synthetic LLDP output",
    )
    conflict = vendor_conflict(
        observed_vendor="Cisco",
        oui_vendor="Intel",
        evidence_ids=[direct.id, oui.id],
    )
    assert conflict is not None
    assert identity_confidence([direct, oui]) == "high"
    assert identity_confidence([direct, oui], [conflict]) == "medium"


def test_conflicting_current_sources_surface_uncertain_reconciliation():
    port = interface(name="SYNTH-NODE-1")
    learned = mac(value="001b.7700.0001")  # IEEE registry: Intel
    result = build_topology(
        hostname="SYNTH-DISCOVERY-SW1",
        model="WS-C3560CG-8PC-S",
        management_ip="192.0.2.10",
        interfaces=[port],
        mac_entries=[learned],
        poe_ports=[],
        cdp_neighbors=[CdpNeighbor(
            remoteName="SYNTH-NODE-1",
            localInterface="Gi0/3",
            platform="Cisco WS-C2960",
            capabilities=["Switch"],
        )],
        observed_at=NOW,
    )
    endpoint = next(item for item in result.devices if item.id != result.root_device_id)
    assert endpoint.conflicts
    assert endpoint.identity_confidence == "medium"

    summary = reconcile(
        device_id=result.root_device_id,
        interfaces=[port],
        ios=CiscoIosEvidenceProvider(
            interfaces=[port],
            mac_entries=[learned],
            observed_at=NOW,
            topology=result,
        ),
        intent=IntentProvider(interfaces=[port]),
        history=HistoryProvider(),
        evaluated_at=NOW,
    )
    assert summary.interfaces[0].status == "uncertain"
    assert summary.interfaces[0].observed is not None
    assert summary.interfaces[0].observed.conflicted is True


def test_freshness_allows_missed_polls_and_only_revocation_becomes_historical():
    assert freshness_for(
        evidence_type="MAC_LEARNED", observed_at=NOW,
        reference_at=NOW + timedelta(seconds=120),
    ) == "current"
    assert freshness_for(
        evidence_type="MAC_LEARNED", observed_at=NOW,
        reference_at=NOW + timedelta(seconds=300),
    ) == "aging"
    assert freshness_for(
        evidence_type="MAC_LEARNED", observed_at=NOW,
        reference_at=NOW + timedelta(hours=2), connection_state="offline",
    ) == "stale"
    assert freshness_for(
        evidence_type="MAC_LEARNED", observed_at=NOW,
        reference_at=NOW + timedelta(seconds=1), revoked=True,
    ) == "historical"


def test_discovery_history_survives_failure_then_revokes_on_success(tmp_path):
    store = DiscoveryHistoryStore(tmp_path / "discovery.sqlite")
    first = store.apply_observation(
        topology(interfaces=[interface()], macs=[mac()]),
        complete=True,
        observed_at=NOW,
    )
    endpoint_id = next(item.id for item in first.devices if item.id != first.root_device_id)

    incomplete = store.apply_observation(
        topology(interfaces=[], at=NOW + timedelta(minutes=1)),
        complete=False,
        observed_at=NOW + timedelta(minutes=1),
        connection_state="reconnecting",
    )
    cached = next(item for item in incomplete.devices if item.id == endpoint_id)
    assert cached.freshness == "aging"
    assert cached.online is False

    complete_down = store.apply_observation(
        topology(
            interfaces=[interface(status="notconnect")],
            at=NOW + timedelta(minutes=6),
        ),
        complete=True,
        observed_at=NOW + timedelta(minutes=6),
    )
    assert not any(item.id == endpoint_id for item in complete_down.devices)
    historical = next(item for item in complete_down.historical_devices if item.id == endpoint_id)
    assert historical.freshness == "historical"
    assert historical.existence_state == "historical"
    prior = next(
        item for item in complete_down.evidence
        if item.evidence_type == "PRIOR_OBSERVATION" and item.entity_id == endpoint_id
    )
    assert prior.revoked is True
    assert prior.freshness == "historical"
    assert store.observation_count(first.root_device_id) > 0


def test_v041_intent_database_migrates_without_losing_rows(tmp_path):
    path = tmp_path / "topology-intent.sqlite"
    with sqlite3.connect(path) as conn:
        conn.executescript(
            """
            CREATE TABLE expected_relationships (
                device_id TEXT NOT NULL, interface TEXT NOT NULL,
                expected_name TEXT NOT NULL, expected_device_type TEXT NOT NULL DEFAULT 'unknown',
                expected_vendor TEXT, expected_model TEXT, source TEXT NOT NULL,
                note TEXT, suppressed INTEGER NOT NULL DEFAULT 0,
                created_at TEXT NOT NULL, updated_at TEXT NOT NULL,
                PRIMARY KEY (device_id, interface)
            );
            CREATE TABLE reconciliation_state (
                device_id TEXT NOT NULL, interface TEXT NOT NULL,
                signature TEXT NOT NULL, status TEXT NOT NULL,
                observed_label TEXT, observed_identified INTEGER NOT NULL DEFAULT 0,
                first_seen TEXT NOT NULL, last_seen TEXT NOT NULL,
                PRIMARY KEY (device_id, interface)
            );
            """
        )
        conn.execute(
            "INSERT INTO expected_relationships VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                "switch-synthetic", "Gi0/4", "SYNTH-AP-01", "access-point",
                None, None, "user-intent", None, 0, NOW.isoformat(), NOW.isoformat(),
            ),
        )

    store = TopologyIntentStore(path)
    assert store.schema_version(path) == 1
    restored = store.list_expected("switch-synthetic")
    assert [(item.interface, item.expected_name) for item in restored] == [
        ("Gi0/4", "SYNTH-AP-01")
    ]
