"""Topology reconciliation.

The governing rule under test: an interface description is *intent*. It may
never become an observed identity, however convenient that would be for the
diagram.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from backend.app.intent_store import TopologyIntentStore
from backend.app.models import (
    ArpEntry,
    CdpNeighbor,
    ExternalSighting,
    InterfaceStatus,
    MacTableEntry,
)
from backend.app.reconciliation import (
    UNIDENTIFIED,
    CiscoIosEvidenceProvider,
    HistoryProvider,
    IntentProvider,
    PreviousInterfaceState,
    is_locally_administered,
    reconcile,
    reconciliation_events,
    reconciliation_signature,
)


NOW = datetime(2026, 8, 22, 16, 0, tzinfo=timezone.utc)
EARLIER = NOW - timedelta(hours=1)


def iface(port: str, name: str = "", status: str = "connected", **kw) -> InterfaceStatus:
    return InterfaceStatus(
        port=port,
        name=name,
        status=status,
        vlan=kw.pop("vlan", "1"),
        speed=kw.pop("speed", "a-1000" if status == "connected" else "auto"),
        duplex=kw.pop("duplex", "a-full" if status == "connected" else "auto"),
        **kw,
    )


def mac(port: str, suffix: str = "01", prefix: str = "0011.2233.44") -> MacTableEntry:
    return MacTableEntry(vlan="1", mac=f"{prefix}{suffix}", type="DYNAMIC", port=port)


def cdp(port: str, name: str, platform: str = "") -> CdpNeighbor:
    return CdpNeighbor(remoteName=name, localInterface=port, platform=platform)


def run(
    interfaces,
    *,
    macs=(),
    neighbors=(),
    arp=(),
    gateway=None,
    stored=(),
    previous=None,
    sightings=(),
):
    ios = CiscoIosEvidenceProvider(
        interfaces=interfaces,
        mac_entries=list(macs),
        cdp_neighbors=list(neighbors),
        arp_entries=list(arp),
        default_gateway=gateway,
        observed_at=NOW,
    )
    return reconcile(
        device_id="switch-physical-test",
        interfaces=interfaces,
        ios=ios,
        intent=IntentProvider(interfaces=interfaces, stored=stored),
        history=HistoryProvider(previous or {}),
        external_sightings=sightings,
        evaluated_at=NOW,
    )


def only(summary):
    return summary.interfaces[0]


@pytest.fixture
def store(tmp_path):
    return TopologyIntentStore(db_path=tmp_path / "intent.sqlite")


# --- 1 & 2: a description is intent, never an observation ------------------


def test_description_alone_produces_expected_identity_not_observed_identity():
    interfaces = [iface("Gi0/1", "Uplink to Test Gateway")]
    result = only(run(interfaces, macs=[mac("Gi0/1")]))

    assert result.expected is not None
    assert result.expected.evidence_class == "expected"
    assert result.expected.source == "interface-description"
    assert result.expected.object_label == "Uplink to Test Gateway"
    # Intent is never an identification, whatever it says.
    assert result.expected.object_identified is False

    assert result.observed is not None
    assert result.observed.evidence_class == "observed"
    # The crux: the observed side does NOT inherit the description.
    assert result.observed.object_label == UNIDENTIFIED
    assert result.observed.object_identified is False
    assert "Test ISP" not in result.observed.object_label


def test_link_up_alone_does_not_prove_the_configured_identity():
    interfaces = [iface("Gi0/1", "Uplink to Test Gateway")]
    result = only(run(interfaces, macs=[mac("Gi0/1"), mac("Gi0/1", "02")]))

    assert result.status == "uncertain"
    assert result.headline == "Present, identity unconfirmed"
    assert "neither confirm nor contradict" in result.explanation
    # Presence is certain even though identity is not.
    assert result.observed.confidence == "high"
    assert result.observed.relationship == "attached-endpoint"


def test_link_up_with_no_learned_addresses_is_still_only_presence():
    result = only(run([iface("Gi0/5", "TV")]))
    assert result.status == "uncertain"
    assert result.observed.object_label == UNIDENTIFIED
    assert result.observed.source == "interface-telemetry"


# --- 3 & 4: CDP is observation and outranks the description ----------------


def test_cdp_neighbour_supplies_the_observed_identity():
    interfaces = [iface("Gi0/1", "Uplink to Test Gateway")]
    result = only(run(interfaces, macs=[mac("Gi0/1")], neighbors=[cdp("Gi0/1", "TEST-GATEWAY-01-HQ", "Meraki TEST-GATEWAY-01")]))

    assert result.observed.object_identified is True
    assert result.observed.source == "cdp"
    assert result.observed.object_label == "TEST-GATEWAY-01-HQ"
    assert result.observed.vendor == "Cisco Meraki"
    assert result.observed.model == "TEST-GATEWAY-01"
    # The description is still held, still labelled as intent.
    assert result.expected.source == "interface-description"


def test_expected_virgin_router_with_observed_mx_is_topology_drift():
    interfaces = [iface("Gi0/1", "Uplink to Test Gateway")]
    result = only(run(interfaces, macs=[mac("Gi0/1")], neighbors=[cdp("Gi0/1", "TEST-GATEWAY-01-HQ", "Meraki TEST-GATEWAY-01")]))

    assert result.status == "drift"
    assert result.drift_kind == "identity"
    assert result.headline == "Topology drift"
    # The explanation must not imply a fault.
    assert "link itself is fine" in result.explanation
    assert "documented topology is out of date" in result.explanation


def test_matching_cdp_identity_is_aligned():
    interfaces = [iface("Gi0/4", "TEST-AP-01 AP")]
    result = only(run(interfaces, macs=[mac("Gi0/4")], neighbors=[cdp("Gi0/4", "TEST-AP-Lab", "TEST-AP-01")]))
    assert result.status == "aligned"
    assert result.drift_kind == "none"


# --- 5: health and reconciliation are independent --------------------------


def test_health_stays_healthy_while_reconciliation_reports_drift():
    """Reconciliation must not reach into health, or vice versa."""
    from backend.app.health_logic import build_summary
    from backend.app.models import (
        CpuStatus,
        EnvironmentStatus,
        InterfaceErrorsResponse,
        MemoryStatus,
        PoeResponse,
    )

    interfaces = [iface("Gi0/1", "Uplink to Test Gateway")]
    summary = build_summary(
        hostname="SWITCHOPS-TEST-SW1",
        model="WS-C3560CG-8PC-S",
        management_ip="192.0.2.10",
        gateway="192.0.2.1",
        ios_version="12.2(55)EX2",
        serial=None,
        uptime="1 day",
        interfaces=interfaces,
        env=EnvironmentStatus(temperatureC=49, state="GREEN"),
        cpu=CpuStatus(cpu5Sec=6),
        memory=MemoryStatus(processorTotal=100, processorUsed=28),
        poe=PoeResponse(availableWatts=124, usedWatts=0, remainingWatts=124, ports=[]),
        errors=InterfaceErrorsResponse(counters=[], totalErrors=0, healthy=True),
        deltas=[],
        evaluated_at=NOW,
    )
    reconciliation = run(interfaces, macs=[mac("Gi0/1")], neighbors=[cdp("Gi0/1", "TEST-GATEWAY-01-HQ", "Meraki TEST-GATEWAY-01")])

    assert summary.health.state == "HEALTHY"
    assert reconciliation.interfaces[0].status == "drift"
    assert reconciliation.attention is True
    # A drifted topology is not an unhealthy one.
    assert summary.healthy is True


# --- 6: an expected device that is absent is not an offline device ---------


def test_expected_device_on_a_down_port_is_missing_not_offline():
    interfaces = [iface("Gi0/4", "TEST-AP-01 AP", status="notconnect")]
    result = only(run(interfaces))

    assert result.status == "expected-not-observed"
    assert result.headline == "Expected device not observed"
    assert result.observed is None
    assert "cannot conclude the device is offline" in result.explanation
    # Nothing anywhere may call it offline.
    blob = result.model_dump_json().lower()
    assert "offline" not in blob.replace("cannot conclude the device is offline", "")


def test_stale_addresses_on_a_down_port_do_not_create_an_observation():
    interfaces = [iface("Gi0/4", "TEST-AP-01 AP", status="notconnect")]
    result = only(run(interfaces, macs=[mac("Gi0/4")]))
    assert result.observed is None
    assert result.status == "expected-not-observed"


def test_disabled_port_with_no_intent_is_not_applicable():
    result = only(run([iface("Gi0/7", "Spare Access Port", status="disabled")]))
    assert result.status == "not-applicable"


# --- 7: historical comparison ---------------------------------------------


def test_neighbour_identity_change_between_observations_is_reported():
    interfaces = [iface("Gi0/1", "Uplink")]
    previous = {"Gi0/1": PreviousInterfaceState(
        connected=True, identity="Test ISPHub5", learned_count=1, observed_at=EARLIER
    )}
    result = only(run(
        interfaces,
        macs=[mac("Gi0/1")],
        neighbors=[cdp("Gi0/1", "TEST-GATEWAY-01-HQ", "Meraki TEST-GATEWAY-01")],
        previous=previous,
    ))
    assert result.changed_since_previous is True
    assert "Test ISPHub5" in result.change_summary
    assert "TEST-GATEWAY-01-HQ" in result.change_summary
    assert result.historical is not None
    assert result.historical.evidence_class == "historical"


def test_link_coming_up_is_a_change_even_when_identity_is_unknown():
    interfaces = [iface("Gi0/3", "Test Server")]
    previous = {"Gi0/3": PreviousInterfaceState(connected=False, observed_at=EARLIER)}
    result = only(run(interfaces, macs=[mac("Gi0/3")], previous=previous))
    assert result.changed_since_previous is True
    assert "came up" in result.change_summary


def test_change_is_orthogonal_to_alignment():
    """An interface can match intent and still have changed."""
    interfaces = [iface("Gi0/4", "TEST-AP-01 AP")]
    previous = {"Gi0/4": PreviousInterfaceState(connected=False, observed_at=EARLIER)}
    result = only(run(
        interfaces,
        macs=[mac("Gi0/4")],
        neighbors=[cdp("Gi0/4", "TEST-AP-Lab", "TEST-AP-01")],
        previous=previous,
    ))
    assert result.status == "aligned"
    assert result.changed_since_previous is True


# --- 8 & 9: event de-duplication ------------------------------------------


def test_repeated_unchanged_drift_produces_one_event(store):
    interfaces = [iface("Gi0/1", "Uplink to Test Gateway")]
    summary = run(interfaces, macs=[mac("Gi0/1")], neighbors=[cdp("Gi0/1", "TEST-GATEWAY-01-HQ", "Meraki TEST-GATEWAY-01")])

    first = reconciliation_events(device_id="sw", summary=summary, store=store, observed_at=NOW)
    assert [event.event_type for event in first] == ["topology_drift_detected"]

    # Twenty more refreshes of the identical situation.
    for index in range(20):
        repeat = reconciliation_events(
            device_id="sw", summary=summary, store=store,
            observed_at=NOW + timedelta(minutes=index + 1),
        )
        assert repeat == [], f"refresh {index} produced a duplicate event"


def test_drift_resolution_produces_exactly_one_resolution_event(store):
    drifting = [iface("Gi0/1", "Uplink to Test Gateway")]
    drift_summary = run(drifting, macs=[mac("Gi0/1")], neighbors=[cdp("Gi0/1", "TEST-GATEWAY-01-HQ", "Meraki TEST-GATEWAY-01")])
    reconciliation_events(device_id="sw", summary=drift_summary, store=store, observed_at=NOW)

    # Intent updated to match what is actually there.
    stored = [store.set_expected(
        device_id="sw", interface="Gi0/1", expected_name="TEST-GATEWAY-01-HQ",
        expected_device_type="router", expected_vendor="Cisco Meraki",
        expected_model="TEST-GATEWAY-01", now=NOW,
    )]
    aligned_summary = run(
        drifting, macs=[mac("Gi0/1")],
        neighbors=[cdp("Gi0/1", "TEST-GATEWAY-01-HQ", "Meraki TEST-GATEWAY-01")], stored=stored,
    )
    assert aligned_summary.interfaces[0].status == "aligned"

    resolved = reconciliation_events(
        device_id="sw", summary=aligned_summary, store=store, observed_at=NOW + timedelta(minutes=5)
    )
    assert [event.event_type for event in resolved] == ["topology_reconciliation_resolved"]

    # And it does not repeat.
    again = reconciliation_events(
        device_id="sw", summary=aligned_summary, store=store, observed_at=NOW + timedelta(minutes=10)
    )
    assert again == []


def test_uncertain_identity_never_generates_events(store):
    """A permanently unidentifiable neighbour is a standing condition."""
    interfaces = [iface("Gi0/1", "Uplink to Test Gateway")]
    summary = run(interfaces, macs=[mac("Gi0/1")])
    assert summary.interfaces[0].status == "uncertain"
    for index in range(5):
        events = reconciliation_events(
            device_id="sw", summary=summary, store=store,
            observed_at=NOW + timedelta(minutes=index),
        )
        assert events == []


def test_changing_drift_shape_produces_a_fresh_event(store):
    interfaces = [iface("Gi0/1", "Uplink to Test Gateway")]
    first = run(interfaces, macs=[mac("Gi0/1")], neighbors=[cdp("Gi0/1", "TEST-GATEWAY-01-HQ", "Meraki TEST-GATEWAY-01")])
    reconciliation_events(device_id="sw", summary=first, store=store, observed_at=NOW)

    second = run(interfaces, macs=[mac("Gi0/1")], neighbors=[cdp("Gi0/1", "OtherBox", "Acme 1000")])
    events = reconciliation_events(
        device_id="sw", summary=second, store=store, observed_at=NOW + timedelta(minutes=5)
    )
    assert "topology_drift_detected" in [event.event_type for event in events]


def test_signature_is_stable_for_the_same_situation():
    interfaces = [iface("Gi0/1", "Uplink to Test Gateway")]
    a = only(run(interfaces, macs=[mac("Gi0/1")]))
    b = only(run(interfaces, macs=[mac("Gi0/1")]))
    assert reconciliation_signature(a) == reconciliation_signature(b)


# --- 10: learned-behind is preserved ---------------------------------------


def test_many_addresses_behind_an_uplink_stay_learned_behind():
    interfaces = [iface("Gi0/1", "Uplink to Test Gateway")]
    macs = [mac("Gi0/1", f"{index:02d}") for index in range(6)]
    result = only(run(interfaces, macs=macs))

    # One observed assertion, not six.
    observed = [a for a in result.assertions if a.evidence_class == "observed" and a.relationship == "attached-endpoint"]
    assert len(observed) == 1
    behind = [a for a in result.assertions if a.relationship == "learned-behind"]
    assert behind, "surplus addresses must be recorded as learned-behind"
    assert "6 addresses" in behind[0].object_label
    assert "behind the device on this port" in behind[0].detail


# --- 11 & 12: intent management -------------------------------------------


def test_user_intent_outranks_the_interface_description(store):
    interfaces = [iface("Gi0/1", "Uplink to Test Gateway")]
    stored = [store.set_expected(
        device_id="sw", interface="Gi0/1", expected_name="TEST-GATEWAY-01-HQ",
        expected_device_type="router", now=NOW,
    )]
    result = only(run(interfaces, macs=[mac("Gi0/1")], stored=stored))
    assert result.expected.source == "user-intent"
    assert result.expected.object_label == "TEST-GATEWAY-01-HQ"
    assert result.expected.confidence == "high"


def test_updating_intent_resolves_the_drift(store):
    interfaces = [iface("Gi0/1", "Uplink to Test Gateway")]
    neighbors = [cdp("Gi0/1", "TEST-GATEWAY-01-HQ", "Meraki TEST-GATEWAY-01")]
    assert only(run(interfaces, macs=[mac("Gi0/1")], neighbors=neighbors)).status == "drift"

    stored = [store.set_expected(
        device_id="sw", interface="Gi0/1", expected_name="TEST-GATEWAY-01-HQ",
        expected_device_type="router", expected_vendor="Cisco Meraki", now=NOW,
    )]
    resolved = only(run(interfaces, macs=[mac("Gi0/1")], neighbors=neighbors, stored=stored))
    assert resolved.status == "aligned"


def test_updating_intent_flags_the_switch_description_as_stale(store):
    """Local intent changes; the device's own documentation does not."""
    interfaces = [iface("Gi0/1", "Uplink to Test Gateway")]
    stored = [store.set_expected(
        device_id="sw", interface="Gi0/1", expected_name="TEST-GATEWAY-01-HQ", now=NOW,
    )]
    result = only(run(interfaces, macs=[mac("Gi0/1")], stored=stored))
    assert result.documentation_stale is True
    # The description is untouched; it is still what the switch reports.
    assert interfaces[0].name == "Uplink to Test Gateway"


def test_intent_store_writes_no_ios(store, monkeypatch):
    """Recording intent must not open a switch session at all."""
    import backend.app.switch_client as switch_client

    def explode():  # pragma: no cover - must never run
        raise AssertionError("recording intent attempted to contact the switch")

    monkeypatch.setattr(switch_client, "get_switch_client", explode)
    store.set_expected(device_id="sw", interface="Gi0/4", expected_name="TEST-AP", now=NOW)
    assert [r.interface for r in store.list_expected("sw")] == ["Gi0/4"]


def test_clearing_intent_falls_back_to_the_description(store):
    store.set_expected(device_id="sw", interface="Gi0/1", expected_name="TEST-GATEWAY-01-HQ", now=NOW)
    assert store.clear_expected(device_id="sw", interface="Gi0/1") is True
    assert store.list_expected("sw") == []

    interfaces = [iface("Gi0/1", "Uplink to Test Gateway")]
    result = only(run(interfaces, macs=[mac("Gi0/1")], stored=store.list_expected("sw")))
    assert result.expected.source == "interface-description"


# --- 13: location drift needs an external source ---------------------------


def test_expected_device_seen_elsewhere_is_location_drift():
    interfaces = [iface("Gi0/4", "TEST-AP-01 AP", status="notconnect")]
    sighting = ExternalSighting(
        label="TEST-AP-01",
        observedLocation="TEST-GATEWAY-01 port 11",
        source="meraki-api",
        confidence="high",
        observedAt=NOW,
    )
    result = only(run(interfaces, sightings=[sighting]))
    assert result.status == "drift"
    assert result.drift_kind == "location"
    assert result.headline == "Location drift"
    assert "TEST-GATEWAY-01 port 11" in result.explanation
    assert "different place" in result.explanation


def test_without_an_external_source_the_same_case_is_only_missing():
    """SwitchOps must not invent a location it cannot see."""
    interfaces = [iface("Gi0/4", "TEST-AP-01 AP", status="notconnect")]
    result = only(run(interfaces))
    assert result.status == "expected-not-observed"
    assert result.drift_kind == "none"
    assert "TEST-GATEWAY-01" not in result.explanation


def test_an_unrelated_sighting_does_not_claim_location_drift():
    interfaces = [iface("Gi0/4", "TEST-AP-01 AP", status="notconnect")]
    sighting = ExternalSighting(label="Some Printer", observedLocation="elsewhere")
    assert only(run(interfaces, sightings=[sighting])).status == "expected-not-observed"


# --- 14 & 16: uncertainty is preserved, conflicts stay honest --------------


def test_uncertain_evidence_is_not_upgraded_to_a_guess():
    interfaces = [iface("Gi0/3", "Test Server")]
    result = only(run(interfaces, macs=[mac("Gi0/3")]))
    assert result.status == "uncertain"
    # Neither aligned nor drift: the evidence supports neither conclusion.
    assert result.status not in {"aligned", "drift"}
    assert result.observed.object_identified is False


def test_description_and_cdp_conflict_keeps_both_claims():
    interfaces = [iface("Gi0/3", "Test Server")]
    result = only(run(interfaces, macs=[mac("Gi0/3")], neighbors=[cdp("Gi0/3", "NAS-BOX", "Synology")]))
    assert result.status == "drift"
    labels = {(a.evidence_class, a.object_label) for a in result.assertions}
    assert ("observed", "NAS-BOX") in labels
    assert ("expected", "Test Server") in labels


def test_unexpected_device_when_no_intent_exists():
    interfaces = [iface("Gi0/6", "")]
    result = only(run(interfaces, macs=[mac("Gi0/6")]))
    assert result.status == "unexpected"
    assert "no intent records what" in result.explanation


# --- 15: inference stays inference ----------------------------------------


def test_locally_administered_addresses_are_flagged_as_uninferable():
    """Randomised addresses close off vendor inference; say so, guess nothing."""
    assert is_locally_administered("0200.0000.0009") is True
    assert is_locally_administered("0200.0000.0007") is True
    assert is_locally_administered("0200.0000.000E") is False

    interfaces = [iface("Gi0/3", "Test Server")]
    result = only(run(interfaces, macs=[mac("Gi0/3", prefix="0e30.9b00.00")]))
    form = [a for a in result.assertions if a.source == "mac-address-form"]
    assert form, "a randomised address should be reported"
    assert form[0].evidence_class == "inferred"
    assert "No vendor can be inferred" in form[0].detail
    # And no vendor is claimed anywhere.
    assert all(a.vendor is None for a in result.assertions if a.evidence_class == "observed")


def test_gateway_path_is_inference_not_identity():
    interfaces = [iface("Gi0/1", "Uplink to Test Gateway")]
    gateway_mac = mac("Gi0/1", "aa")
    result = only(run(
        interfaces,
        macs=[gateway_mac],
        arp=[ArpEntry(ip="192.0.2.1", mac=gateway_mac.mac, ageMinutes=3, interface="Vlan1")],
        gateway="192.0.2.1",
    ))
    path = [a for a in result.assertions if a.relationship == "gateway-path"]
    assert len(path) == 1
    assert path[0].evidence_class == "inferred"
    assert path[0].object_identified is False
    assert "does not identify the device" in path[0].detail
    # A path is not an identity: the interface remains unidentified.
    assert result.observed.object_identified is False
    assert result.status == "uncertain"


def test_gateway_absent_from_arp_yields_no_path_claim():
    """The gateway routinely ages out. Absence must claim nothing."""
    interfaces = [iface("Gi0/1", "Uplink to Test Gateway")]
    result = only(run(
        interfaces,
        macs=[mac("Gi0/1")],
        arp=[ArpEntry(ip="192.0.2.95", mac="0200.0000.000F", ageMinutes=60, interface="Vlan1")],
        gateway="192.0.2.1",
    ))
    assert [a for a in result.assertions if a.relationship == "gateway-path"] == []


# --- 17: persistence across restart ---------------------------------------


def test_intent_and_reconciliation_state_survive_restart(tmp_path):
    path = tmp_path / "intent.sqlite"
    first = TopologyIntentStore(db_path=path)
    first.set_expected(device_id="sw", interface="Gi0/1", expected_name="TEST-GATEWAY-01-HQ", now=NOW)
    first.observe_reconciliation(
        device_id="sw", interface="Gi0/1", signature="drift|identity|A|B", status="drift", now=NOW
    )

    # New process, same file.
    second = TopologyIntentStore(db_path=path)
    assert [r.expected_name for r in second.list_expected("sw")] == ["TEST-GATEWAY-01-HQ"]
    changed, previous, _first_seen = second.observe_reconciliation(
        device_id="sw", interface="Gi0/1", signature="drift|identity|A|B", status="drift", now=NOW
    )
    assert changed is False
    assert previous == "drift|identity|A|B"


def test_first_seen_is_preserved_while_a_discrepancy_persists(tmp_path):
    store = TopologyIntentStore(db_path=tmp_path / "intent.sqlite")
    store.observe_reconciliation(
        device_id="sw", interface="Gi0/1", signature="drift|identity|A|B", status="drift", now=NOW
    )
    _changed, _previous, first_seen = store.observe_reconciliation(
        device_id="sw", interface="Gi0/1", signature="drift|identity|A|B",
        status="drift", now=NOW + timedelta(hours=3),
    )
    assert first_seen == NOW


# --- 18 & 19: nothing leaks -----------------------------------------------


def test_reconciliation_output_contains_no_hardware_addresses():
    interfaces = [iface("Gi0/1", "Uplink to Test Gateway"), iface("Gi0/2", "Test Workstation")]
    macs = [mac("Gi0/1", "01", prefix="aabb.ccdd.ee"), mac("Gi0/2", "02", prefix="1122.3344.55")]
    summary = run(interfaces, macs=macs)
    blob = summary.model_dump_json()
    for entry in macs:
        assert entry.mac not in blob
    assert "aabb.ccdd" not in blob
    assert "1122.3344" not in blob


def test_intent_store_persists_no_hardware_addresses(tmp_path):
    path = tmp_path / "intent.sqlite"
    store = TopologyIntentStore(db_path=path)
    store.set_expected(device_id="sw", interface="Gi0/1", expected_name="TEST-GATEWAY-01-HQ", now=NOW)
    store.observe_reconciliation(
        device_id="sw", interface="Gi0/1",
        signature="uncertain|none|Unidentified device|Uplink to Test Gateway",
        status="uncertain", now=NOW,
    )
    raw = path.read_bytes()
    # No colon- or dot-formatted address shape anywhere in the file.
    import re as _re
    assert not _re.search(rb"[0-9a-f]{4}\.[0-9a-f]{4}\.[0-9a-f]{4}", raw, _re.IGNORECASE)
    assert not _re.search(rb"([0-9a-f]{2}:){5}[0-9a-f]{2}", raw, _re.IGNORECASE)


# --- 20: the physical lab, as it actually is now ---------------------------


def _physical_lab():
    """The real Catalyst after the Test ISP -> TEST-GATEWAY-01 migration.

    Descriptions still describe the old network; that is the whole point.
    """
    return [
        iface("Gi0/1", "Uplink to Test Gateway", protected=True),
        iface("Gi0/2", "Test Workstation", protected=True),
        iface("Gi0/3", "Test Server"),
        iface("Gi0/4", "TEST-AP-01 AP", status="notconnect"),
        iface("Gi0/5", "TV", status="notconnect"),
        iface("Gi0/6", "Spare Access Port", status="disabled"),
        iface("Gi0/7", "Spare Access Port", status="disabled"),
        iface("Gi0/8", "Spare Access Port", status="disabled"),
        iface("Gi0/9", "Spare Uplink", status="disabled"),
        iface("Gi0/10", "Spare", status="disabled"),
    ]


def test_physical_lab_reconciles_the_way_the_evidence_allows():
    """No CDP on this switch, so upstream identity is genuinely unknowable."""
    interfaces = _physical_lab()
    macs = [
        mac("Gi0/1", "01"), mac("Gi0/1", "02"),
        mac("Gi0/2", "03"),
        mac("Gi0/3", "04", prefix="0e30.9b00.00"), mac("Gi0/3", "05"),
    ]
    summary = run(interfaces, macs=macs)
    by_port = {r.interface: r.status for r in summary.interfaces}

    assert by_port["Gi0/1"] == "uncertain"
    assert by_port["Gi0/2"] == "uncertain"
    assert by_port["Gi0/3"] == "uncertain"
    assert by_port["Gi0/4"] == "expected-not-observed"
    assert by_port["Gi0/5"] == "expected-not-observed"
    for port in ("Gi0/6", "Gi0/7", "Gi0/8", "Gi0/9", "Gi0/10"):
        assert by_port[port] == "not-applicable"

    assert summary.uncertain == 3
    assert summary.expected_not_observed == 2
    assert summary.drift == 0
    assert summary.attention is True
    assert "expected but not observed" in summary.headline

    # Nothing anywhere claims the upstream is an MX, or that the AP is offline.
    blob = summary.model_dump_json()
    assert "TEST-GATEWAY" not in blob
    assert "Test ISP" in blob  # only as recorded intent
    virgin = next(a for r in summary.interfaces if r.interface == "Gi0/1"
                  for a in r.assertions if "Test ISP" in a.object_label)
    assert virgin.evidence_class == "expected"


def test_physical_lab_upstream_never_becomes_an_observed_router():
    """The v0.2.1 shape rendered Gi0/1 as an observed router named from the
    description. That must be impossible now."""
    interfaces = _physical_lab()
    summary = run(interfaces, macs=[mac("Gi0/1", "01"), mac("Gi0/1", "02")])
    gi01 = next(r for r in summary.interfaces if r.interface == "Gi0/1")
    observed = [a for a in gi01.assertions if a.evidence_class == "observed"]
    assert observed
    for assertion in observed:
        assert assertion.object_identified is False
        assert "Test ISP" not in assertion.object_label
        assert assertion.device_type is None


# --- suppression ("ignore this interface") ---------------------------------


def test_muting_an_interface_stops_it_asking_for_attention(store):
    interfaces = [iface("Gi0/4", "TEST-AP-01 AP", status="notconnect")]
    assert only(run(interfaces)).status == "expected-not-observed"

    stored = [store.set_expected(
        device_id="sw", interface="Gi0/4", expected_name="TEST-AP-01 AP",
        suppressed=True, now=NOW,
    )]
    summary = run(interfaces, stored=stored)
    result = only(summary)
    assert result.status == "not-applicable"
    assert result.headline == "Muted"
    assert summary.attention is False
    assert summary.expected_not_observed == 0


def test_muting_stops_events_and_resolves_an_open_one(store):
    interfaces = [iface("Gi0/4", "TEST-AP-01 AP", status="notconnect")]
    first = run(interfaces)
    opened = reconciliation_events(device_id="sw", summary=first, store=store, observed_at=NOW)
    assert [event.event_type for event in opened] == ["expected_device_missing"]

    stored = [store.set_expected(
        device_id="sw", interface="Gi0/4", expected_name="TEST-AP-01 AP",
        suppressed=True, now=NOW,
    )]
    muted = run(interfaces, stored=stored)
    closed = reconciliation_events(
        device_id="sw", summary=muted, store=store, observed_at=NOW + timedelta(minutes=1)
    )
    assert [event.event_type for event in closed] == ["topology_reconciliation_resolved"]

    for index in range(5):
        assert reconciliation_events(
            device_id="sw", summary=muted, store=store,
            observed_at=NOW + timedelta(minutes=index + 2),
        ) == []


def test_muting_still_records_what_is_observed(store):
    """Muting hides the prompt, not the evidence."""
    interfaces = [iface("Gi0/1", "Uplink to Test Gateway")]
    stored = [store.set_expected(
        device_id="sw", interface="Gi0/1", expected_name="Uplink to Test Gateway",
        suppressed=True, now=NOW,
    )]
    result = only(run(interfaces, macs=[mac("Gi0/1")], stored=stored))
    assert result.status == "not-applicable"
    assert result.observed is not None
    assert result.observed.evidence_class == "observed"
