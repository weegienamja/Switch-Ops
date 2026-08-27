"""Observation-only coordinator for physical acceptance tests.

SwitchOps performs no part of the physical action. A human moves the cable.
This module decides whether a proposed test is safe to describe at all, tells
the operator exactly what to do, waits for real evidence, runs the ordinary
production reconciliation over it, and reports PASS/FAIL against explicit
acceptance criteria.

It is development tooling: not imported by ``backend.app``, no API route, no
device write path, no host mutation. Its only capability is reading topology
observations that the normal collectors already produce.

The safety gate is the important part. A destination port is only proposed when
evidence positively establishes it is a free access port. Absence of evidence is
treated as unsafe, never as permission -- moving a management uplink because a
description happened to be blank is not a recoverable mistake.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any, Callable, Literal, Protocol, Sequence

from pydantic import BaseModel, Field

from backend.app.topology import classify_interface_role


PortSafetyBlocker = Literal[
    "PORT_NOT_FOUND",
    "PORT_OCCUPIED",
    "PORT_IS_UPLINK_OR_TRUNK",
    "PORT_CARRIES_MANAGEMENT_PATH",
    "PORT_ADMINISTRATIVELY_DOWN",
    "PORT_ROLE_NOT_ESTABLISHED",
    "PORT_IS_THE_SOURCE_PORT",
    "PORT_IS_INFRASTRUCTURE_ATTACHMENT",
]

AcceptanceOutcome = Literal["PASS", "FAIL", "INDETERMINATE"]

Readiness = Literal["READY", "BLOCKED", "INDETERMINATE"]

#: Why a physical test cannot be offered yet. Each names something the operator
#: has to change in the world, not something the tooling can decide to ignore.
Prerequisite = Literal[
    "LIVE_CATALYST_TOPOLOGY_UNAVAILABLE",
    "TOPOLOGY_EVIDENCE_STALE",
    "BASELINE_ENDPOINT_NOT_IDENTIFIED",
    "BASELINE_ATTACHMENT_NOT_ESTABLISHED",
    "NO_SAFE_DESTINATION_PORT",
]

#: How the attachment evidence was obtained.
EvidenceSource = Literal["live-catalyst", "durable-history", "none"]

# Statuses that mean "nothing is plugged in here right now".
_FREE_STATUSES = {"notconnect", "disabled", "notconnected"}
_ADMIN_DOWN_STATUSES = {"disabled", "err-disabled", "errdisable"}

# Device categories that are infrastructure rather than an endpoint. Reused
# from the production classifier rather than restated, so the physical tooling
# cannot drift into a second opinion about what a neighbour is.
_INFRASTRUCTURE_TYPES = {"switch", "router", "access-point"}

#: Attachment state changes the instant somebody moves a cable, so evidence
#: older than this cannot authorise a physical test. Chosen to be shorter than
#: the slow discovery tier so a readiness verdict always rests on a deliberate
#: recent observation.
MAX_EVIDENCE_AGE = timedelta(minutes=5)


class PortSafetyAssessment(BaseModel):
    """Whether one destination port may be offered to the operator."""

    port: str
    safe: bool
    blockers: list[PortSafetyBlocker] = Field(default_factory=list)
    evidence: list[str] = Field(default_factory=list)

    model_config = {"populate_by_name": True}


class AcceptanceFinding(BaseModel):
    criterion: str
    passed: bool
    expected: str
    actual: str


class AcceptanceReport(BaseModel):
    test_id: str = Field(alias="testId")
    outcome: AcceptanceOutcome
    generated_at: datetime = Field(alias="generatedAt")
    operator_action: str = Field(alias="operatorAction")
    findings: list[AcceptanceFinding] = Field(default_factory=list)
    writes_performed: Literal[0] = Field(default=0, alias="writesPerformed")

    model_config = {"populate_by_name": True}


class TopologyObservation(Protocol):
    """The subset of a reconciled topology this coordinator reads."""

    root_device_id: str
    devices: Sequence[Any]
    transitions: Sequence[Any]
    historical_devices: Sequence[Any]


def _normalize_mac(value: str | None) -> str | None:
    if not value:
        return None
    cleaned = "".join(char for char in value.lower() if char.isalnum())
    return cleaned or None


def _endpoints(observation: TopologyObservation) -> list[Any]:
    return [
        device
        for device in observation.devices
        if device.id != observation.root_device_id
    ]


def find_endpoint_by_mac(
    observation: TopologyObservation, mac: str
) -> Any | None:
    target = _normalize_mac(mac)
    for device in _endpoints(observation):
        if _normalize_mac(getattr(device, "mac", None)) == target:
            return device
    return None


def infrastructure_ports(observation: TopologyObservation) -> set[str]:
    """Ports where production classification found infrastructure, not an endpoint.

    Derived from the reconciled topology rather than re-derived here: a second
    classifier inside the test tooling could disagree with the product about
    what a neighbour is, and the disagreement would only ever be discovered by
    unplugging the wrong cable.
    """
    found: set[str] = set()
    for device in observation.devices:
        if device.id == observation.root_device_id:
            continue
        port = getattr(device, "connected_interface", None)
        if port and getattr(device, "type", "unknown") in _INFRASTRUCTURE_TYPES:
            found.add(port)
    return found


def assess_destination_port(
    port: str,
    *,
    interfaces: Sequence[Any],
    mac_entries: Sequence[Any],
    source_port: str | None = None,
    management_interface: str | None = None,
    infrastructure: Sequence[str] = (),
) -> PortSafetyAssessment:
    """Decide whether a port is a safe destination for a manual cable move.

    Every blocker is derived from positive evidence or from the *absence* of
    evidence that the port is a free access port. The default answer is unsafe.
    """
    blockers: list[PortSafetyBlocker] = []
    evidence: list[str] = []

    match = next(
        (item for item in interfaces if getattr(item, "port", None) == port), None
    )
    if match is None:
        return PortSafetyAssessment(
            port=port,
            safe=False,
            blockers=["PORT_NOT_FOUND"],
            evidence=[f"{port} does not appear in the observed interface list."],
        )

    if source_port and port == source_port:
        blockers.append("PORT_IS_THE_SOURCE_PORT")
        evidence.append(f"{port} is where the endpoint already is.")

    if management_interface and port == management_interface:
        blockers.append("PORT_CARRIES_MANAGEMENT_PATH")
        evidence.append(
            f"{port} is the interface SwitchOps currently reaches the Catalyst through."
        )

    if port in set(infrastructure):
        blockers.append("PORT_IS_INFRASTRUCTURE_ATTACHMENT")
        evidence.append(
            f"{port} has a switch, router, or access point attached according to "
            "production topology classification."
        )

    status = (getattr(match, "status", "") or "").strip().lower()
    vlan = (getattr(match, "vlan", "") or "").strip()
    description = (getattr(match, "name", "") or "").strip()

    # A description is operator intent, not proof of the port's present role,
    # so it can only ever add a blocker. It never clears one: "Access Port" on
    # a port that is trunking or occupied is still unsafe.
    role = classify_interface_role(description, vlan)
    if role == "uplink":
        blockers.append("PORT_IS_UPLINK_OR_TRUNK")
        evidence.append(
            f"{port} classifies as an uplink or trunk (vlan={vlan or 'unknown'}, "
            f"description={description or 'none'})."
        )
    elif role != "access":
        # An unlabelled access port is common and fine, but only when the other
        # evidence positively shows it is a free, non-trunk access port.
        if vlan.lower() == "trunk" or not status:
            blockers.append("PORT_ROLE_NOT_ESTABLISHED")
            evidence.append(
                f"{port} has no evidence establishing it as an access port."
            )
        else:
            evidence.append(
                f"{port} carries no uplink description and is on access vlan {vlan}."
            )

    if status in _ADMIN_DOWN_STATUSES:
        # Bringing it up would be a device write, which is out of scope.
        blockers.append("PORT_ADMINISTRATIVELY_DOWN")
        evidence.append(f"{port} is {status}; enabling it would require a write.")
    elif status not in _FREE_STATUSES:
        blockers.append("PORT_OCCUPIED")
        evidence.append(f"{port} reports status {status or 'unknown'}, not free.")
    else:
        evidence.append(f"{port} reports status {status}, so nothing is attached.")

    learned = [
        entry
        for entry in mac_entries
        if getattr(entry, "port", None) == port
    ]
    if learned:
        if "PORT_OCCUPIED" not in blockers:
            blockers.append("PORT_OCCUPIED")
        evidence.append(
            f"{port} has {len(learned)} learned MAC address(es); another endpoint uses it."
        )
    else:
        evidence.append(f"{port} has no learned MAC addresses.")

    return PortSafetyAssessment(
        port=port,
        safe=not blockers,
        blockers=blockers,
        evidence=evidence,
    )


def choose_destination_port(
    *,
    interfaces: Sequence[Any],
    mac_entries: Sequence[Any],
    source_port: str,
    management_interface: str | None = None,
    infrastructure: Sequence[str] = (),
) -> tuple[str | None, list[PortSafetyAssessment]]:
    """Return the first port that is positively safe, plus every assessment.

    Returning all assessments matters: when nothing is safe, the operator needs
    to see why rather than a bare refusal.
    """
    assessments = [
        assess_destination_port(
            getattr(item, "port", ""),
            interfaces=interfaces,
            mac_entries=mac_entries,
            source_port=source_port,
            management_interface=management_interface,
            infrastructure=infrastructure,
        )
        for item in interfaces
    ]
    safe = next((item for item in assessments if item.safe), None)
    return (safe.port if safe else None), assessments


class ReadinessReport(BaseModel):
    """Whether a physical test may be offered to the operator at all.

    Produced without touching anything. An `operatorAction` is present only
    when every prerequisite passed; a blocked report deliberately carries no
    instruction, because a half-safe instruction is the dangerous output here.
    """

    test_id: str = Field(alias="testId")
    readiness: Readiness
    evidence_source: EvidenceSource = Field(alias="evidenceSource")
    evidence_observed_at: datetime | None = Field(default=None, alias="evidenceObservedAt")
    evidence_age_seconds: int | None = Field(default=None, alias="evidenceAgeSeconds")
    evidence_fresh: bool = Field(default=False, alias="evidenceFresh")
    #: Pseudonymous endpoint identity. Production derives this id by hashing
    #: the hardware address, so it is safe to display and to log.
    baseline_endpoint_id: str | None = Field(default=None, alias="baselineEndpointId")
    current_attachment: str | None = Field(default=None, alias="currentAttachment")
    candidate_destinations: list[str] = Field(
        default_factory=list, alias="candidateDestinations"
    )
    port_assessments: list[PortSafetyAssessment] = Field(
        default_factory=list, alias="portAssessments"
    )
    blockers: list[Prerequisite] = Field(default_factory=list)
    required_prerequisite: str | None = Field(default=None, alias="requiredPrerequisite")
    operator_action: str | None = Field(default=None, alias="operatorAction")
    #: Readiness evaluation only reads observations that were already
    #: collected, so this is a structural guarantee rather than a measurement.
    writes_performed: Literal[0] = Field(default=0, alias="writesPerformed")

    model_config = {"populate_by_name": True}


PREREQUISITE_TEXT: dict[Prerequisite, str] = {
    "LIVE_CATALYST_TOPOLOGY_UNAVAILABLE": (
        "Restore read-only Catalyst management connectivity. Endpoint-to-port "
        "attachment is only observable from the device's MAC address table, "
        "which requires a working management path."
    ),
    "TOPOLOGY_EVIDENCE_STALE": (
        "Collect a fresh topology observation. Attachment changes the moment a "
        "cable moves, so stale evidence cannot authorise a physical test."
    ),
    "BASELINE_ENDPOINT_NOT_IDENTIFIED": (
        "Confirm the endpoint under test is observed on the Catalyst before "
        "moving anything."
    ),
    "BASELINE_ATTACHMENT_NOT_ESTABLISHED": (
        "Establish which port the endpoint is currently attached to."
    ),
    "NO_SAFE_DESTINATION_PORT": (
        "Provide a destination port that is positively established as a free, "
        "non-trunk, non-uplink access port."
    ),
}


def evaluate_endpoint_move_readiness(
    *,
    observation: TopologyObservation | None,
    endpoint_mac: str | None,
    evidence_source: EvidenceSource,
    evidence_observed_at: datetime | None,
    now: datetime,
    management_interface: str | None = None,
    interfaces: Sequence[Any] = (),
    mac_entries: Sequence[Any] = (),
) -> ReadinessReport:
    """Decide whether ENDPOINT_PORT_MOVE can be run right now.

    Absence of evidence is a blocker, never a pass. The most common real
    outcome is BLOCKED on live Catalyst topology, because the only source of
    endpoint attachment is the device's own MAC address table.
    """
    blockers: list[Prerequisite] = []
    age_seconds: int | None = None
    fresh = False

    if evidence_source != "live-catalyst" or observation is None:
        # Durable history can describe where an endpoint used to be. It cannot
        # observe where it is now, which is the whole point of the test.
        blockers.append("LIVE_CATALYST_TOPOLOGY_UNAVAILABLE")
    else:
        if evidence_observed_at is None:
            blockers.append("TOPOLOGY_EVIDENCE_STALE")
        else:
            age = now - evidence_observed_at
            age_seconds = int(age.total_seconds())
            fresh = timedelta(0) <= age <= MAX_EVIDENCE_AGE
            if not fresh:
                blockers.append("TOPOLOGY_EVIDENCE_STALE")

    endpoint = None
    attachment: str | None = None
    if observation is not None and endpoint_mac:
        endpoint = find_endpoint_by_mac(observation, endpoint_mac)
    if endpoint is None:
        blockers.append("BASELINE_ENDPOINT_NOT_IDENTIFIED")
    else:
        attachment = getattr(endpoint, "connected_interface", None)
        if not attachment:
            blockers.append("BASELINE_ATTACHMENT_NOT_ESTABLISHED")

    assessments: list[PortSafetyAssessment] = []
    candidates: list[str] = []
    # Candidate selection is only meaningful against fresh live evidence; with
    # stale or absent evidence a "free" port may already be occupied.
    if attachment and fresh and observation is not None:
        _, assessments = choose_destination_port(
            interfaces=interfaces,
            mac_entries=mac_entries,
            source_port=attachment,
            management_interface=management_interface,
            infrastructure=sorted(infrastructure_ports(observation)),
        )
        candidates = [item.port for item in assessments if item.safe]
        if not candidates:
            blockers.append("NO_SAFE_DESTINATION_PORT")

    ordered = list(dict.fromkeys(blockers))
    readiness: Readiness = "READY" if not ordered else "BLOCKED"
    action: str | None = None
    if readiness == "READY" and attachment and candidates:
        action = (
            f"Move the endpoint's Ethernet cable from {attachment} to "
            f"{candidates[0]}. SwitchOps will not change anything; it only "
            "observes."
        )

    return ReadinessReport(
        testId="ENDPOINT_PORT_MOVE",
        readiness=readiness,
        evidenceSource=evidence_source,
        evidenceObservedAt=evidence_observed_at,
        evidenceAgeSeconds=age_seconds,
        evidenceFresh=fresh,
        baselineEndpointId=getattr(endpoint, "id", None),
        currentAttachment=attachment,
        candidateDestinations=candidates,
        portAssessments=assessments,
        blockers=ordered,
        requiredPrerequisite=PREREQUISITE_TEXT[ordered[0]] if ordered else None,
        operatorAction=action,
    )


def evaluate_endpoint_move(
    *,
    baseline: TopologyObservation,
    observed: TopologyObservation,
    endpoint_mac: str,
    source_port: str,
    destination_port: str,
) -> list[AcceptanceFinding]:
    """Compare a post-move observation against the endpoint-move criteria."""
    findings: list[AcceptanceFinding] = []

    def record(criterion: str, passed: bool, expected: str, actual: str) -> None:
        findings.append(
            AcceptanceFinding(
                criterion=criterion, passed=passed, expected=expected, actual=actual
            )
        )

    baseline_endpoint = find_endpoint_by_mac(baseline, endpoint_mac)
    moved_endpoint = find_endpoint_by_mac(observed, endpoint_mac)

    if baseline_endpoint is None:
        record(
            "baseline-endpoint-present",
            False,
            f"the endpoint {endpoint_mac} is observed before the move",
            "the endpoint was not present in the baseline",
        )
        return findings

    if moved_endpoint is None:
        # The MAC vanished. That is not evidence of a move to anywhere.
        record(
            "endpoint-observed-after-move",
            False,
            f"{endpoint_mac} is observed after the move",
            "the endpoint is not present in the current observation",
        )
        return findings

    record(
        "identity-retained",
        moved_endpoint.id == baseline_endpoint.id,
        f"the same endpoint identity {baseline_endpoint.id}",
        str(moved_endpoint.id),
    )

    record(
        "current-attachment-updated",
        getattr(moved_endpoint, "connected_interface", None) == destination_port,
        f"current attachment {destination_port}",
        str(getattr(moved_endpoint, "connected_interface", None)),
    )

    record(
        "previous-attachment-retained",
        getattr(moved_endpoint, "previous_connected_interface", None) == source_port,
        f"previous attachment {source_port}",
        str(getattr(moved_endpoint, "previous_connected_interface", None)),
    )

    # Attachment is mutable state, so it must be *re-stated* after a move
    # rather than inherited. Identity is not mutable state, so its confidence
    # must not degrade merely because the cable moved.
    record(
        "attachment-state-reflects-the-move",
        getattr(moved_endpoint, "attachment_state", "unknown") == "moved",
        "attachment state 'moved'",
        str(getattr(moved_endpoint, "attachment_state", "unknown")),
    )

    order = ["unknown", "low", "medium", "high", "confirmed"]

    def rank(value: str) -> int:
        return order.index(value) if value in order else 0

    before = str(getattr(baseline_endpoint, "confidence", "unknown"))
    after = str(getattr(moved_endpoint, "confidence", "unknown"))
    record(
        "identity-confidence-not-degraded-by-a-move",
        rank(after) >= rank(before),
        f"identity confidence at least {before}",
        after,
    )

    endpoint_ids = [device.id for device in _endpoints(observed)]
    duplicates = len(endpoint_ids) - len(set(endpoint_ids))
    record(
        "no-duplicate-endpoint",
        duplicates == 0,
        "0 duplicate endpoint identities",
        f"{duplicates} duplicate endpoint identities",
    )

    kinds = [getattr(item, "kind", "") for item in observed.transitions]
    record(
        "no-false-replacement",
        "DEVICE_REPLACED" not in kinds,
        "no DEVICE_REPLACED claim",
        ", ".join(kinds) or "no transitions",
    )

    if "ATTACHMENT_CONFLICT" in kinds:
        # The same MAC visible in two places is a real, expected outcome that
        # must be reported as ambiguous rather than resolved by guessing.
        record(
            "attachment-conflict-reported-as-indeterminate",
            True,
            "an ambiguous attachment is reported, not guessed",
            "ATTACHMENT_CONFLICT",
        )

    return findings


class PhysicalAcceptanceCoordinator:
    """Sequence one observation-only physical acceptance test.

    The coordinator never mutates anything. ``observe`` is expected to return a
    reconciled topology from the ordinary read-only collectors; ``write_probe``
    reports how many write operations occurred so a nonzero count fails the run.
    """

    def __init__(
        self,
        *,
        observe: Callable[[], TopologyObservation],
        write_probe: Callable[[], int] | None = None,
        now: Callable[[], datetime] | None = None,
    ) -> None:
        self._observe = observe
        self._write_probe = write_probe or (lambda: 0)
        self._now = now or (lambda: datetime.now(timezone.utc))

    def capture_baseline(self) -> TopologyObservation:
        return self._observe()

    def wait_for_change(
        self,
        *,
        endpoint_mac: str,
        source_port: str,
        timeout: timedelta,
        sleep: Callable[[float], None] | None = None,
        interval_seconds: float = 5.0,
    ) -> TopologyObservation | None:
        """Poll read-only until the endpoint leaves its source port."""
        deadline = self._now() + timeout
        while True:
            observation = self._observe()
            endpoint = find_endpoint_by_mac(observation, endpoint_mac)
            if endpoint is not None:
                attachment = getattr(endpoint, "connected_interface", None)
                if attachment and attachment != source_port:
                    return observation
            if self._now() >= deadline:
                return None
            if sleep is not None:
                sleep(interval_seconds)

    def run_endpoint_move(
        self,
        *,
        endpoint_mac: str,
        source_port: str,
        destination_port: str,
        baseline: TopologyObservation,
        observed: TopologyObservation | None,
    ) -> AcceptanceReport:
        action = (
            f"Move the PC Ethernet cable from {source_port} to {destination_port}. "
            "SwitchOps will not change anything; it only observes."
        )
        writes = self._write_probe()

        if observed is None:
            return AcceptanceReport(
                testId="ENDPOINT_PORT_MOVE",
                outcome="INDETERMINATE",
                generatedAt=self._now(),
                operatorAction=action,
                findings=[
                    AcceptanceFinding(
                        criterion="endpoint-move-observed",
                        passed=False,
                        expected=f"the endpoint appears on {destination_port}",
                        actual="no qualifying change was observed before the deadline",
                    )
                ],
            )

        findings = evaluate_endpoint_move(
            baseline=baseline,
            observed=observed,
            endpoint_mac=endpoint_mac,
            source_port=source_port,
            destination_port=destination_port,
        )
        findings.append(
            AcceptanceFinding(
                criterion="zero-writes",
                passed=writes == 0,
                expected="0 write operations",
                actual=f"{writes} write operations",
            )
        )
        outcome: AcceptanceOutcome = (
            "PASS" if all(item.passed for item in findings) else "FAIL"
        )
        return AcceptanceReport(
            testId="ENDPOINT_PORT_MOVE",
            outcome=outcome,
            generatedAt=self._now(),
            operatorAction=action,
            findings=findings,
        )


# --- development CLI -------------------------------------------------------
#
# Read-only. It performs a GET against the loopback backend and prints a
# readiness verdict. It has no code path that writes to the host, the Catalyst,
# or Meraki, and it never instructs the operator unless every prerequisite
# passed.


def readiness_from_dashboard(
    payload: dict[str, Any] | None,
    *,
    endpoint_mac: str,
    now: datetime,
    management_interface: str | None = None,
) -> ReadinessReport:
    """Build a readiness verdict from a normal read-only dashboard response.

    Production models do the parsing, so this tool cannot develop its own
    opinion about what the device reported.
    """
    from backend.app.models import InterfaceStatus, MacTableEntry, TopologyModel

    if not payload or "topology" not in payload:
        # No dashboard means no MAC address table, which means attachment is
        # simply not observable right now.
        return evaluate_endpoint_move_readiness(
            observation=None,
            endpoint_mac=endpoint_mac,
            evidence_source="none",
            evidence_observed_at=None,
            now=now,
        )

    topology = TopologyModel.model_validate(payload["topology"])
    interfaces = [
        InterfaceStatus.model_validate(item)
        for item in payload.get("interfaces", {}).get("interfaces", [])
    ]
    macs = [
        MacTableEntry.model_validate(item)
        for item in payload.get("macTable", {}).get("entries", [])
    ]
    return evaluate_endpoint_move_readiness(
        observation=topology,
        endpoint_mac=endpoint_mac,
        evidence_source="live-catalyst",
        evidence_observed_at=topology.generated_at,
        now=now,
        management_interface=management_interface,
        interfaces=interfaces,
        mac_entries=macs,
    )


def _render(report: ReadinessReport) -> str:
    lines = [
        f"Scenario:            {report.test_id}",
        f"Readiness:           {report.readiness}",
        f"Evidence source:     {report.evidence_source}",
        f"Evidence freshness:  {'fresh' if report.evidence_fresh else 'stale or absent'}",
    ]
    if report.evidence_age_seconds is not None:
        lines.append(f"Evidence age:        {report.evidence_age_seconds}s")
    lines.append(f"Baseline endpoint:   {report.baseline_endpoint_id or '-'}")
    lines.append(f"Current attachment:  {report.current_attachment or '-'}")
    lines.append(
        "Candidate ports:     "
        + (", ".join(report.candidate_destinations) or "none established as safe")
    )
    if report.blockers:
        lines.append("Blockers:")
        lines.extend(f"  - {code}" for code in report.blockers)
    if report.required_prerequisite:
        lines.append(f"Required first:      {report.required_prerequisite}")
    if report.operator_action:
        lines.append(f"Operator action:     {report.operator_action}")
    else:
        lines.append("Operator action:     none (prerequisites not satisfied)")
    lines.append(f"Writes performed:    {report.writes_performed}")
    if report.port_assessments:
        lines.append("Port assessments:")
        for item in report.port_assessments:
            verdict = "SAFE" if item.safe else ", ".join(item.blockers)
            lines.append(f"  {item.port:<10} {verdict}")
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    import argparse
    import json
    import urllib.error
    import urllib.request

    parser = argparse.ArgumentParser(
        prog="python -m backend.resilience_lab.physical_acceptance",
        description=(
            "Evaluate whether the ENDPOINT_PORT_MOVE physical acceptance test "
            "can run right now. Observation only: nothing is changed."
        ),
    )
    parser.add_argument(
        "--endpoint-mac",
        required=True,
        help="Hardware address of the endpoint under test (never stored).",
    )
    parser.add_argument("--backend", default="http://127.0.0.1:8765")
    parser.add_argument("--management-interface", default=None)
    parser.add_argument("--json", action="store_true", help="Emit the report as JSON.")
    args = parser.parse_args(argv)

    payload: dict[str, Any] | None = None
    try:
        with urllib.request.urlopen(
            f"{args.backend}/api/switch/dashboard", timeout=30
        ) as response:
            payload = json.loads(response.read().decode("utf-8"))
    except (urllib.error.URLError, urllib.error.HTTPError, TimeoutError, OSError):
        # An unreachable device is the expected case while the management path
        # is degraded, and is reported as a prerequisite rather than an error.
        payload = None

    report = readiness_from_dashboard(
        payload,
        endpoint_mac=args.endpoint_mac,
        now=datetime.now(timezone.utc),
        management_interface=args.management_interface,
    )
    if args.json:
        print(report.model_dump_json(by_alias=True, indent=2))
    else:
        print(_render(report))
    return 0 if report.readiness == "READY" else 1


if __name__ == "__main__":
    raise SystemExit(main())
