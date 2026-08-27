"""The physical acceptance coordinator observes; the operator acts.

The safety tests matter most. Proposing the wrong destination port would have a
human unplug a live uplink, so absence of evidence must never read as
permission.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone

from backend.resilience_lab.physical_acceptance import (
    PhysicalAcceptanceCoordinator,
    assess_destination_port,
    choose_destination_port,
    evaluate_endpoint_move,
)


@dataclass
class Iface:
    port: str
    status: str = "notconnect"
    vlan: str = "10"
    name: str = ""


@dataclass
class Mac:
    mac: str
    port: str


@dataclass
class Device:
    id: str
    mac: str | None = None
    connected_interface: str | None = None
    previous_connected_interface: str | None = None
    attachment_state: str = "current"
    attachment_confidence: str = "high"
    confidence: str = "high"


@dataclass
class Transition:
    kind: str


@dataclass
class Topology:
    root_device_id: str = "switch-root"
    devices: list = field(default_factory=list)
    transitions: list = field(default_factory=list)
    historical_devices: list = field(default_factory=list)


FREE = Iface("Gi0/5", status="notconnect", vlan="10", name="")
OCCUPIED = Iface("Gi0/6", status="connected", vlan="10", name="")
TRUNK = Iface("Gi0/24", status="connected", vlan="trunk", name="")
UPLINK_DESC = Iface("Gi0/23", status="notconnect", vlan="10", name="Spine Uplink")
SOURCE = Iface("Gi0/2", status="connected", vlan="10", name="")


def test_a_free_unlabelled_access_port_is_safe():
    result = assess_destination_port(
        "Gi0/5", interfaces=[SOURCE, FREE], mac_entries=[], source_port="Gi0/2"
    )
    assert result.safe is True
    assert result.blockers == []


def test_a_port_with_a_learned_mac_is_refused():
    result = assess_destination_port(
        "Gi0/6",
        interfaces=[SOURCE, OCCUPIED],
        mac_entries=[Mac("0000.5e00.53ff", "Gi0/6")],
        source_port="Gi0/2",
    )
    assert result.safe is False
    assert "PORT_OCCUPIED" in result.blockers


def test_a_trunk_is_refused():
    result = assess_destination_port(
        "Gi0/24", interfaces=[SOURCE, TRUNK], mac_entries=[], source_port="Gi0/2"
    )
    assert result.safe is False
    assert "PORT_IS_UPLINK_OR_TRUNK" in result.blockers


def test_an_uplink_description_is_refused_even_when_the_port_is_free():
    result = assess_destination_port(
        "Gi0/23", interfaces=[SOURCE, UPLINK_DESC], mac_entries=[], source_port="Gi0/2"
    )
    assert result.safe is False
    assert "PORT_IS_UPLINK_OR_TRUNK" in result.blockers


def test_the_management_interface_is_refused():
    result = assess_destination_port(
        "Gi0/5",
        interfaces=[SOURCE, FREE],
        mac_entries=[],
        source_port="Gi0/2",
        management_interface="Gi0/5",
    )
    assert result.safe is False
    assert "PORT_CARRIES_MANAGEMENT_PATH" in result.blockers


def test_the_source_port_is_not_offered_as_a_destination():
    result = assess_destination_port(
        "Gi0/2", interfaces=[SOURCE], mac_entries=[], source_port="Gi0/2"
    )
    assert result.safe is False
    assert "PORT_IS_THE_SOURCE_PORT" in result.blockers


def test_an_unknown_port_is_refused():
    result = assess_destination_port(
        "Gi9/99", interfaces=[SOURCE], mac_entries=[], source_port="Gi0/2"
    )
    assert result.safe is False
    assert result.blockers == ["PORT_NOT_FOUND"]


def test_a_port_with_no_status_evidence_is_refused_rather_than_assumed_free():
    blank = Iface("Gi0/9", status="", vlan="", name="")
    result = assess_destination_port(
        "Gi0/9", interfaces=[SOURCE, blank], mac_entries=[], source_port="Gi0/2"
    )
    assert result.safe is False
    assert "PORT_ROLE_NOT_ESTABLISHED" in result.blockers


def test_an_administratively_down_port_is_refused_because_enabling_is_a_write():
    disabled = Iface("Gi0/8", status="err-disabled", vlan="10", name="")
    result = assess_destination_port(
        "Gi0/8", interfaces=[SOURCE, disabled], mac_entries=[], source_port="Gi0/2"
    )
    assert result.safe is False
    assert "PORT_ADMINISTRATIVELY_DOWN" in result.blockers


def test_choose_destination_reports_every_assessment_when_nothing_is_safe():
    chosen, assessments = choose_destination_port(
        interfaces=[SOURCE, TRUNK, OCCUPIED],
        mac_entries=[Mac("0000.5e00.53ff", "Gi0/6")],
        source_port="Gi0/2",
    )
    assert chosen is None
    assert {item.port for item in assessments} == {"Gi0/2", "Gi0/24", "Gi0/6"}
    assert all(not item.safe for item in assessments)


def test_choose_destination_picks_the_safe_port():
    chosen, _ = choose_destination_port(
        interfaces=[SOURCE, TRUNK, FREE], mac_entries=[], source_port="Gi0/2"
    )
    assert chosen == "Gi0/5"


# --- acceptance evaluation -------------------------------------------------

MAC = "0000.5e00.530a"


def _baseline() -> Topology:
    return Topology(devices=[Device(id="endpoint-a", mac=MAC, connected_interface="Gi0/2")])


def _moved() -> Topology:
    return Topology(
        devices=[
            Device(
                id="endpoint-a",
                mac=MAC,
                connected_interface="Gi0/5",
                previous_connected_interface="Gi0/2",
                attachment_state="moved",
            )
        ],
        transitions=[Transition("ENDPOINT_MOVED")],
    )


def test_a_clean_port_move_passes_every_criterion():
    findings = evaluate_endpoint_move(
        baseline=_baseline(),
        observed=_moved(),
        endpoint_mac=MAC,
        source_port="Gi0/2",
        destination_port="Gi0/5",
    )
    assert all(item.passed for item in findings), [
        (item.criterion, item.actual) for item in findings if not item.passed
    ]


def test_a_vanished_mac_is_not_reported_as_a_move():
    # MAC A disappears and nothing replaces it. Claiming a move to the
    # destination would be an assertion the evidence does not support.
    findings = evaluate_endpoint_move(
        baseline=_baseline(),
        observed=Topology(devices=[]),
        endpoint_mac=MAC,
        source_port="Gi0/2",
        destination_port="Gi0/5",
    )
    assert [item.criterion for item in findings] == ["endpoint-observed-after-move"]
    assert findings[0].passed is False


def test_a_different_mac_appearing_is_not_a_move_of_the_original():
    observed = Topology(
        devices=[Device(id="endpoint-b", mac="0000.5e00.530b", connected_interface="Gi0/5")],
        transitions=[Transition("DEVICE_REPLACED")],
    )
    findings = evaluate_endpoint_move(
        baseline=_baseline(),
        observed=observed,
        endpoint_mac=MAC,
        source_port="Gi0/2",
        destination_port="Gi0/5",
    )
    assert any(
        item.criterion == "endpoint-observed-after-move" and not item.passed
        for item in findings
    )


def test_a_replacement_claim_fails_the_move_acceptance():
    observed = _moved()
    observed.transitions = [Transition("DEVICE_REPLACED")]
    findings = evaluate_endpoint_move(
        baseline=_baseline(),
        observed=observed,
        endpoint_mac=MAC,
        source_port="Gi0/2",
        destination_port="Gi0/5",
    )
    failed = {item.criterion for item in findings if not item.passed}
    assert "no-false-replacement" in failed


def test_the_same_mac_on_two_ports_is_reported_as_ambiguous():
    observed = Topology(
        devices=[
            Device(
                id="endpoint-a",
                mac=MAC,
                connected_interface="Gi0/5",
                previous_connected_interface="Gi0/2",
            )
        ],
        transitions=[Transition("ATTACHMENT_CONFLICT")],
    )
    findings = evaluate_endpoint_move(
        baseline=_baseline(),
        observed=observed,
        endpoint_mac=MAC,
        source_port="Gi0/2",
        destination_port="Gi0/5",
    )
    assert any(
        item.criterion == "attachment-conflict-reported-as-indeterminate"
        for item in findings
    )


# --- coordinator -----------------------------------------------------------

def test_coordinator_reports_pass_and_zero_writes():
    coordinator = PhysicalAcceptanceCoordinator(
        observe=_moved,
        now=lambda: datetime(2026, 8, 27, tzinfo=timezone.utc),
    )
    report = coordinator.run_endpoint_move(
        endpoint_mac=MAC,
        source_port="Gi0/2",
        destination_port="Gi0/5",
        baseline=_baseline(),
        observed=_moved(),
    )
    assert report.outcome == "PASS"
    assert report.writes_performed == 0
    assert "SwitchOps will not change anything" in report.operator_action


def test_coordinator_fails_the_run_when_any_write_is_reported():
    coordinator = PhysicalAcceptanceCoordinator(
        observe=_moved,
        write_probe=lambda: 1,
        now=lambda: datetime(2026, 8, 27, tzinfo=timezone.utc),
    )
    report = coordinator.run_endpoint_move(
        endpoint_mac=MAC,
        source_port="Gi0/2",
        destination_port="Gi0/5",
        baseline=_baseline(),
        observed=_moved(),
    )
    assert report.outcome == "FAIL"
    assert any(
        item.criterion == "zero-writes" and not item.passed for item in report.findings
    )


def test_no_observed_change_is_indeterminate_not_a_failure():
    coordinator = PhysicalAcceptanceCoordinator(
        observe=_baseline,
        now=lambda: datetime(2026, 8, 27, tzinfo=timezone.utc),
    )
    report = coordinator.run_endpoint_move(
        endpoint_mac=MAC,
        source_port="Gi0/2",
        destination_port="Gi0/5",
        baseline=_baseline(),
        observed=None,
    )
    assert report.outcome == "INDETERMINATE"


def test_wait_for_change_returns_none_when_the_endpoint_never_moves():
    clock = {"now": datetime(2026, 8, 27, tzinfo=timezone.utc)}

    def now() -> datetime:
        return clock["now"]

    def sleep(seconds: float) -> None:
        clock["now"] += timedelta(seconds=seconds)

    coordinator = PhysicalAcceptanceCoordinator(observe=_baseline, now=now)
    assert (
        coordinator.wait_for_change(
            endpoint_mac=MAC,
            source_port="Gi0/2",
            timeout=timedelta(seconds=20),
            sleep=sleep,
        )
        is None
    )


def test_wait_for_change_returns_the_observation_once_the_cable_moves():
    states = [_baseline(), _baseline(), _moved()]

    coordinator = PhysicalAcceptanceCoordinator(
        observe=lambda: states.pop(0),
        now=lambda: datetime(2026, 8, 27, tzinfo=timezone.utc),
    )
    observed = coordinator.wait_for_change(
        endpoint_mac=MAC,
        source_port="Gi0/2",
        timeout=timedelta(seconds=60),
        sleep=lambda _seconds: None,
    )
    assert observed is not None
    endpoint = observed.devices[0]
    assert endpoint.connected_interface == "Gi0/5"


# --- readiness / dry run ---------------------------------------------------

from datetime import datetime as _dt  # noqa: E402

from backend.resilience_lab.physical_acceptance import (  # noqa: E402
    MAX_EVIDENCE_AGE,
    evaluate_endpoint_move_readiness,
    infrastructure_ports,
)

NOW = _dt(2026, 8, 27, 12, 0, 0, tzinfo=timezone.utc)


@dataclass
class TypedDevice:
    id: str
    type: str = "desktop"
    mac: str | None = None
    connected_interface: str | None = None
    previous_connected_interface: str | None = None


def _live_topology() -> Topology:
    return Topology(
        devices=[TypedDevice(id="device-abc123", mac=MAC, connected_interface="Gi0/2")]
    )


def _readiness(**overrides):
    kwargs = dict(
        observation=_live_topology(),
        endpoint_mac=MAC,
        evidence_source="live-catalyst",
        evidence_observed_at=NOW,
        now=NOW,
        interfaces=[SOURCE, FREE, TRUNK],
        mac_entries=[Mac(MAC, "Gi0/2")],
    )
    kwargs.update(overrides)
    return evaluate_endpoint_move_readiness(**kwargs)


def test_fresh_live_evidence_with_a_safe_port_is_ready():
    report = _readiness()
    assert report.readiness == "READY"
    assert report.blockers == []
    assert report.current_attachment == "Gi0/2"
    assert report.candidate_destinations == ["Gi0/5"]
    assert report.operator_action and "Gi0/2" in report.operator_action
    assert "Gi0/5" in report.operator_action
    assert report.writes_performed == 0


def test_no_live_topology_source_is_blocked_on_catalyst_connectivity():
    # The real current state: management is degraded, so the MAC address table
    # cannot be read and attachment is unobservable.
    report = _readiness(evidence_source="none", observation=None)
    assert report.readiness == "BLOCKED"
    assert "LIVE_CATALYST_TOPOLOGY_UNAVAILABLE" in report.blockers
    assert report.operator_action is None
    assert report.required_prerequisite
    assert "management" in report.required_prerequisite.lower()


def test_durable_history_alone_cannot_authorise_the_test():
    # History can say where the endpoint used to be; it cannot observe where
    # it is now, which is the entire question the test asks.
    report = _readiness(evidence_source="durable-history")
    assert report.readiness == "BLOCKED"
    assert "LIVE_CATALYST_TOPOLOGY_UNAVAILABLE" in report.blockers
    assert report.operator_action is None


def test_stale_topology_evidence_cannot_authorise_the_test():
    stale = NOW - MAX_EVIDENCE_AGE - timedelta(seconds=1)
    report = _readiness(evidence_observed_at=stale)
    assert report.readiness == "BLOCKED"
    assert "TOPOLOGY_EVIDENCE_STALE" in report.blockers
    assert report.evidence_fresh is False
    assert report.operator_action is None
    # A stale run must not publish candidates either: a "free" port may have
    # been occupied since the observation.
    assert report.candidate_destinations == []


def test_evidence_without_a_timestamp_is_treated_as_stale():
    report = _readiness(evidence_observed_at=None)
    assert report.readiness == "BLOCKED"
    assert "TOPOLOGY_EVIDENCE_STALE" in report.blockers


def test_an_unobserved_endpoint_blocks_the_test():
    report = _readiness(observation=Topology(devices=[]))
    assert report.readiness == "BLOCKED"
    assert "BASELINE_ENDPOINT_NOT_IDENTIFIED" in report.blockers
    assert report.baseline_endpoint_id is None


def test_an_endpoint_with_no_established_attachment_blocks_the_test():
    report = _readiness(
        observation=Topology(devices=[TypedDevice(id="device-abc123", mac=MAC)]),
    )
    assert report.readiness == "BLOCKED"
    assert "BASELINE_ATTACHMENT_NOT_ESTABLISHED" in report.blockers


def test_no_safe_destination_blocks_the_test():
    report = _readiness(interfaces=[SOURCE, TRUNK, OCCUPIED])
    assert report.readiness == "BLOCKED"
    assert "NO_SAFE_DESTINATION_PORT" in report.blockers
    assert report.operator_action is None
    # Every rejection is still explained.
    assert {item.port for item in report.port_assessments} == {
        "Gi0/2",
        "Gi0/24",
        "Gi0/6",
    }


def test_readiness_reports_a_pseudonymous_endpoint_identity_only():
    report = _readiness()
    assert report.baseline_endpoint_id == "device-abc123"
    serialized = report.model_dump_json()
    assert MAC not in serialized
    assert MAC.replace(".", "") not in serialized


def test_infrastructure_ports_reuse_production_device_classification():
    observation = Topology(
        devices=[
            TypedDevice(id="device-sw", type="switch", connected_interface="Gi0/23"),
            TypedDevice(id="device-ap", type="access-point", connected_interface="Gi0/22"),
            TypedDevice(id="device-pc", type="desktop", connected_interface="Gi0/2"),
        ]
    )
    assert infrastructure_ports(observation) == {"Gi0/23", "Gi0/22"}


def test_a_port_with_infrastructure_attached_is_never_a_candidate():
    observation = Topology(
        devices=[
            TypedDevice(id="device-abc123", mac=MAC, connected_interface="Gi0/2"),
            TypedDevice(id="device-ap", type="access-point", connected_interface="Gi0/5"),
        ]
    )
    report = _readiness(observation=observation, interfaces=[SOURCE, FREE])
    assert report.readiness == "BLOCKED"
    assert "NO_SAFE_DESTINATION_PORT" in report.blockers
    rejected = next(item for item in report.port_assessments if item.port == "Gi0/5")
    assert "PORT_IS_INFRASTRUCTURE_ATTACHMENT" in rejected.blockers


def test_an_access_port_description_cannot_clear_a_real_blocker():
    # Intent is not proof of present role.
    labelled = Iface("Gi0/6", status="connected", vlan="10", name="Access Port")
    result = assess_destination_port(
        "Gi0/6",
        interfaces=[SOURCE, labelled],
        mac_entries=[Mac("0000.5e00.53ff", "Gi0/6")],
        source_port="Gi0/2",
    )
    assert result.safe is False
    assert "PORT_OCCUPIED" in result.blockers


def test_a_readiness_report_never_performs_writes():
    assert _readiness().writes_performed == 0
    assert _readiness(evidence_source="none", observation=None).writes_performed == 0


# --- identity and attachment semantics -------------------------------------

def test_attachment_state_must_be_restated_after_a_move():
    # Attachment is mutable state. Leaving it as "current" after a cable move
    # would mean topology never noticed the move at all.
    observed = _moved()
    observed.devices[0].attachment_state = "current"
    findings = evaluate_endpoint_move(
        baseline=_baseline(),
        observed=observed,
        endpoint_mac=MAC,
        source_port="Gi0/2",
        destination_port="Gi0/5",
    )
    failed = {item.criterion for item in findings if not item.passed}
    assert "attachment-state-reflects-the-move" in failed


def test_identity_confidence_must_not_degrade_because_a_cable_moved():
    # Identity is not mutable state: moving a port says nothing about whether
    # we still know which endpoint this is.
    observed = _moved()
    observed.devices[0].confidence = "low"
    findings = evaluate_endpoint_move(
        baseline=_baseline(),
        observed=observed,
        endpoint_mac=MAC,
        source_port="Gi0/2",
        destination_port="Gi0/5",
    )
    failed = {item.criterion for item in findings if not item.passed}
    assert "identity-confidence-not-degraded-by-a-move" in failed


def test_identity_confidence_may_improve_after_a_move():
    observed = _moved()
    observed.devices[0].confidence = "confirmed"
    findings = evaluate_endpoint_move(
        baseline=_baseline(),
        observed=observed,
        endpoint_mac=MAC,
        source_port="Gi0/2",
        destination_port="Gi0/5",
    )
    assert all(item.passed for item in findings)


# --- dashboard adapter -----------------------------------------------------

def test_a_missing_dashboard_is_blocked_not_an_error():
    from backend.resilience_lab.physical_acceptance import readiness_from_dashboard

    report = readiness_from_dashboard(None, endpoint_mac=MAC, now=NOW)
    assert report.readiness == "BLOCKED"
    assert "LIVE_CATALYST_TOPOLOGY_UNAVAILABLE" in report.blockers
    assert report.evidence_source == "none"
    assert report.writes_performed == 0


def test_a_dashboard_without_topology_is_blocked():
    from backend.resilience_lab.physical_acceptance import readiness_from_dashboard

    report = readiness_from_dashboard({"interfaces": {}}, endpoint_mac=MAC, now=NOW)
    assert report.readiness == "BLOCKED"
    assert "LIVE_CATALYST_TOPOLOGY_UNAVAILABLE" in report.blockers


def test_a_live_dashboard_is_parsed_with_production_models():
    from backend.resilience_lab.physical_acceptance import readiness_from_dashboard

    generated = NOW.isoformat().replace("+00:00", "Z")
    payload = {
        "topology": {
            "generatedAt": generated,
            "rootDeviceId": "switch-root",
            "devices": [
                {
                    "id": "switch-root",
                    "type": "switch",
                    "name": "switch",
                    "source": "observed",
                    "confidence": "high",
                    "classificationStage": "model",
                    "visualCategory": "switch",
                    "online": True,
                },
                {
                    "id": "device-abc123",
                    "type": "desktop",
                    "name": "endpoint",
                    "mac": MAC,
                    "source": "observed",
                    "confidence": "high",
                    "classificationStage": "category",
                    "visualCategory": "desktop",
                    "online": True,
                    "connectedInterface": "Gi0/2",
                },
            ],
            "interfaces": [],
            "links": [],
        },
        "interfaces": {
            "interfaces": [
                {"port": "Gi0/2", "status": "connected", "vlan": "10"},
                {"port": "Gi0/5", "status": "notconnect", "vlan": "10"},
            ]
        },
        "macTable": {
            "entries": [{"vlan": "10", "mac": MAC, "type": "DYNAMIC", "port": "Gi0/2"}]
        },
    }
    report = readiness_from_dashboard(payload, endpoint_mac=MAC, now=NOW)
    assert report.evidence_source == "live-catalyst"
    assert report.readiness == "READY"
    assert report.current_attachment == "Gi0/2"
    assert report.candidate_destinations == ["Gi0/5"]
    assert report.baseline_endpoint_id == "device-abc123"
    assert MAC not in report.model_dump_json()
