"""Deterministic orchestration over real SwitchOps production reasoning."""
from __future__ import annotations

from datetime import timedelta
import gc
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
from typing import Callable

from backend.app.discovery import LocalAdapter, correlate_local_endpoint
from backend.app.discovery_store import DiscoveryHistoryStore
from backend.app.management_path import (
    HostAddressObservation,
    ManagementPathObservation,
    ManagementPathService,
    ManagementPathStore,
    ManagementRoute,
)
from backend.app.meraki_management import (
    MerakiLanEvidence,
    MerakiManagementEvidence,
)
from backend.app.models import InterfaceStatus, MacTableEntry
from backend.app.recovery_plan import RecoveryPlan, validate_recovery_plan_binding
from backend.app.topology import build_topology

from .models import (
    ActualPhaseOutcome,
    AssertionResult,
    ManagementEvidence,
    MerakiScenarioEvidence,
    PhaseExpectation,
    ResilienceScenario,
    ScenarioPhase,
    ScenarioPhaseResult,
    ScenarioRunResult,
    TopologyEvidence,
)
from .provider import ImmutableScenarioProvider


WriteProbe = Callable[[str, str], int]


class _NoLegacyHistory:
    def latest_successful_observation_at(self, _device: str):
        return None

    def management_context_for_target(self, _target: str):
        return None

    def latest_local_host(self, _device: str):
        return None


class _ScenarioObserver:
    def __init__(self) -> None:
        self.current: ManagementPathObservation | None = None

    def observe(self, target: str):
        if self.current is None:
            raise RuntimeError("No management evidence is active.")
        if target != getattr(self, "target", target):
            raise RuntimeError("Scenario target binding changed unexpectedly.")
        return SimpleNamespace(public=self.current.model_copy(deep=True), adapter_mac=None)


def _management_observation(
    evidence: ManagementEvidence, phase: ScenarioPhase
) -> ManagementPathObservation:
    addresses = []
    if evidence.source_ip and evidence.prefix_length is not None:
        addresses.append(
            HostAddressObservation(
                address=evidence.source_ip,
                prefixLength=evidence.prefix_length,
                prefixOrigin="Dhcp" if evidence.dhcp_enabled else "Manual",
                addressState="Preferred",
                skipAsSource=False,
            )
        )
    lease = evidence.dhcp_lease_obtained
    if lease is None and evidence.dhcp_enabled:
        lease = phase.at - timedelta(minutes=5)
    return ManagementPathObservation(
        observedAt=phase.at,
        supported=evidence.supported,
        collectionError=evidence.collection_error,
        adapterId=evidence.adapter_id,
        adapterName=evidence.adapter_name,
        interfaceIndex=evidence.interface_index,
        interfaceMetric=evidence.interface_metric,
        adapterState=evidence.adapter_state,
        sourceIp=evidence.source_ip,
        prefixLength=evidence.prefix_length,
        connectedPrefix=evidence.connected_prefix,
        targetOnConnectedPrefix=evidence.target_on_connected_prefix,
        dhcpEnabled=evidence.dhcp_enabled,
        dhcpStaticCoexistence=evidence.dhcp_static_coexistence,
        adapterAddresses=addresses,
        dhcpServer=evidence.dhcp_server,
        dhcpLeaseObtained=lease,
        defaultGateway=evidence.default_gateway,
        route=ManagementRoute(
            destinationPrefix=evidence.route.destination_prefix,
            nextHop=evidence.route.next_hop,
            kind=evidence.route.kind,
            routeMetric=evidence.route.route_metric,
            protocol=evidence.route.protocol,
        ),
        windowsConnectivity=evidence.windows_connectivity,
        tcp22=evidence.tcp22,
        icmpReachable=evidence.icmp_reachable,
    )


def _meraki_evidence(
    evidence: MerakiScenarioEvidence | None, phase: ScenarioPhase
) -> MerakiManagementEvidence:
    selected = evidence or MerakiScenarioEvidence()
    observed_at = phase.at if selected.freshness != "historical" else None
    return MerakiManagementEvidence(
        state=selected.state,
        checkedAt=phase.at,
        observedAt=observed_at,
        freshness=selected.freshness,
        complete=selected.complete,
        detail=selected.detail,
        failedOperations=selected.failed_operations,
        lans=[
            MerakiLanEvidence(
                subnet=item.subnet,
                vlanId=item.vlan_id,
                applianceIp=item.appliance_ip,
                dhcpMode=item.dhcp_mode,
            )
            for item in selected.lans
        ],
    )


def _topology_observation(
    evidence: TopologyEvidence,
    *,
    phase: ScenarioPhase,
    store: DiscoveryHistoryStore,
):
    interfaces = [
        InterfaceStatus(
            port=item.port,
            status=item.status,
            vlan=item.vlan,
            name=item.name,
            speed=item.speed,
            duplex=item.duplex,
        )
        for item in evidence.interfaces
    ]
    macs = [
        MacTableEntry(vlan=item.vlan, mac=item.mac, type="DYNAMIC", port=item.port)
        for item in evidence.macs
    ]
    adapters = [
        LocalAdapter(
            name=item.name,
            ip=item.ip,
            netmask=item.netmask,
            mac=item.mac,
        )
        for item in evidence.adapters
    ]
    local_endpoint = correlate_local_endpoint(
        management_ip=evidence.management_ip,
        mac_entries=macs,
        arp_entries=[],
        interfaces=interfaces,
        adapters=adapters,
    )
    topology = build_topology(
        hostname=evidence.hostname,
        model=evidence.model,
        management_ip=evidence.management_ip,
        interfaces=interfaces,
        mac_entries=macs,
        poe_ports=[],
        local_endpoint=local_endpoint,
        observed_at=phase.at,
        source_namespace="resilience-scenario",
    )
    return store.apply_observation(
        topology,
        complete=evidence.complete,
        observed_at=phase.at,
        connection_state="live" if evidence.complete else "stale",
    )


def _claims(actual: ActualPhaseOutcome) -> list[str]:
    claims: list[str] = []
    if actual.management_diagnosis:
        claims.append(actual.management_diagnosis)
        if actual.management_diagnosis == "MANAGEMENT_PATH_HEALTHY":
            claims.append("MANAGEMENT_PATH_HEALTHY")
        else:
            claims.append("MANAGEMENT_PATH_DEGRADED")
    if actual.confidence == "INDETERMINATE":
        claims.append("INDETERMINATE")
    if actual.recovery_plan_status:
        claims.append(f"RECOVERY_PLAN_{actual.recovery_plan_status}")
    claims.extend(actual.recovery_blockers)
    claims.extend(actual.topology_transitions)
    if actual.identity_retained is True:
        claims.append("IDENTITY_RETAINED")
    elif actual.identity_retained is False:
        claims.append("IDENTITY_REPLACED")
    if "ATTACHMENT_CONFLICT" in actual.topology_transitions:
        claims.append("INDETERMINATE")
    if actual.meraki_state == "unavailable":
        claims.append("MERAKI_API_UNAVAILABLE")
    return list(dict.fromkeys(claims))


def _assertions(
    expected: PhaseExpectation, actual: ActualPhaseOutcome
) -> list[AssertionResult]:
    results: list[AssertionResult] = []

    def check(dimension, label: str, expected_value, actual_value) -> None:
        results.append(
            AssertionResult(
                dimension=dimension,
                expectation=f"{label}={expected_value}",
                actual=str(actual_value),
                passed=actual_value == expected_value,
            )
        )

    if expected.management_diagnosis is not None:
        check(
            "classification",
            "management diagnosis",
            expected.management_diagnosis,
            actual.management_diagnosis,
        )
    if expected.confidence is not None:
        check("classification", "confidence", expected.confidence, actual.confidence)
    if expected.recovery_plan_status is not None:
        check(
            "recovery-planning",
            "recovery plan",
            expected.recovery_plan_status,
            actual.recovery_plan_status,
        )
    if expected.meraki_state is not None:
        check("classification", "Meraki state", expected.meraki_state, actual.meraki_state)
    if expected.meraki_freshness is not None:
        check(
            "historical-continuity",
            "Meraki freshness",
            expected.meraki_freshness,
            actual.meraki_freshness,
        )
    if expected.last_known_good_observed_at is not None:
        check(
            "historical-continuity",
            "last known good timestamp",
            expected.last_known_good_observed_at,
            actual.last_known_good_observed_at,
        )
    if expected.binding_valid is not None:
        check(
            "recovery-planning",
            "prior plan binding valid",
            expected.binding_valid,
            actual.binding_valid,
        )
    if expected.topology_transition is not None:
        results.append(
            AssertionResult(
                dimension="topology-reconciliation",
                expectation=f"transition includes {expected.topology_transition}",
                actual=", ".join(actual.topology_transitions) or "none",
                passed=expected.topology_transition in actual.topology_transitions,
            )
        )
    if expected.identity_retained is not None:
        check(
            "identity-retention",
            "identity retained",
            expected.identity_retained,
            actual.identity_retained,
        )
    if expected.duplicate_entity_ids is not None:
        check(
            "identity-retention",
            "duplicate entity IDs",
            expected.duplicate_entity_ids,
            actual.duplicate_entity_ids,
        )
    if expected.current_attachment is not None:
        check(
            "topology-reconciliation",
            "current attachment",
            expected.current_attachment,
            actual.current_attachment,
        )
    if expected.previous_attachment is not None:
        check(
            "historical-continuity",
            "previous attachment",
            expected.previous_attachment,
            actual.previous_attachment,
        )
    if expected.historical_entity_count_at_least is not None:
        results.append(
            AssertionResult(
                dimension="historical-continuity",
                expectation=(
                    "historical entity count >= "
                    f"{expected.historical_entity_count_at_least}"
                ),
                actual=str(actual.historical_entity_count),
                passed=(
                    actual.historical_entity_count
                    >= expected.historical_entity_count_at_least
                ),
            )
        )
    for claim in expected.must_claim:
        results.append(
            AssertionResult(
                dimension="classification",
                expectation=f"MUST CLAIM {claim}",
                actual=", ".join(actual.claims) or "none",
                passed=claim in actual.claims,
            )
        )
    for claim in expected.must_not_claim:
        results.append(
            AssertionResult(
                dimension="unsafe-action-prevention",
                expectation=f"MUST NOT CLAIM {claim}",
                actual=", ".join(actual.claims) or "none",
                passed=claim not in actual.claims,
            )
        )
    check(
        "unsafe-action-prevention",
        "writes performed",
        expected.writes_performed,
        actual.writes_performed,
    )
    return results


class ResilienceScenarioRunner:
    """Run ordered synthetic evidence through production services and stores."""

    reasoning_modules = [
        "backend.app.management_path.ManagementPathService",
        "backend.app.management_path.diagnose_management_path",
        "backend.app.management_path.apply_meraki_context",
        "backend.app.recovery_plan.build_recovery_plan",
        "backend.app.recovery_plan.validate_recovery_plan_binding",
        "backend.app.discovery.correlate_local_endpoint",
        "backend.app.topology.build_topology",
        "backend.app.discovery_store.DiscoveryHistoryStore",
    ]

    def __init__(self, *, write_probe: WriteProbe | None = None) -> None:
        self._write_probe = write_probe or (lambda _scenario, _phase: 0)

    def run(self, scenario: ResilienceScenario) -> ScenarioRunResult:
        provider = ImmutableScenarioProvider(scenario)
        phase_results: list[ScenarioPhaseResult] = []
        plans: dict[str, RecoveryPlan] = {}
        # The explicit reference release below makes normal Windows cleanup
        # deterministic. If production reasoning raises first, do not let an
        # antivirus/sqlite handle race mask that original scenario failure.
        with TemporaryDirectory(
            prefix="switchops-resilience-", ignore_cleanup_errors=True
        ) as temporary:
            root = Path(temporary)
            management_db = root / "management.sqlite"
            topology_db = root / "topology.sqlite"
            observer = _ScenarioObserver()
            active_meraki = MerakiManagementEvidence.unavailable(
                checked_at=scenario.phases[0].at,
                state="not-configured",
                detail="Synthetic Meraki evidence is not configured.",
            )
            legacy = _NoLegacyHistory()

            def meraki_provider() -> MerakiManagementEvidence:
                return active_meraki.model_copy(deep=True)

            def management_service() -> ManagementPathService:
                return ManagementPathService(
                    observer=observer,
                    store=ManagementPathStore(management_db),
                    telemetry_store=legacy,
                    configuration_store=legacy,
                    discovery_store=legacy,
                    meraki_provider=meraki_provider,
                )

            service = management_service()
            topology_store = DiscoveryHistoryStore(topology_db)

            for declared in scenario.phases:
                phase = provider.advance(declared.id)
                if phase.restart_backend:
                    service = management_service()
                    topology_store = DiscoveryHistoryStore(topology_db)
                actual = ActualPhaseOutcome(
                    writesPerformed=self._write_probe(scenario.id, phase.id)
                )

                management = phase.evidence.management
                if management is not None:
                    observer.target = management.target
                    observer.current = _management_observation(management, phase)
                    active_meraki = _meraki_evidence(phase.evidence.meraki, phase)
                    response = service.assess(
                        management.target,
                        {
                            "state": management.session_state,
                            "errorCode": management.session_error_code,
                        },
                    )
                    actual.management_diagnosis = response.diagnosis.conclusion
                    actual.confidence = response.diagnosis.confidence
                    actual.recovery_plan_status = response.recovery_plan.status
                    actual.recovery_blockers = [
                        blocker.code for blocker in response.recovery_plan.blockers
                    ]
                    actual.meraki_state = response.meraki_evidence.state
                    actual.meraki_freshness = response.meraki_evidence.freshness
                    actual.last_known_good_observed_at = (
                        response.last_known_good.observed_at
                        if response.last_known_good is not None
                        else None
                    )
                    source_phase = phase.expected.binding_from_phase
                    if source_phase is not None:
                        prior = plans.get(source_phase)
                        if prior is not None:
                            actual.binding_valid = validate_recovery_plan_binding(
                                prior,
                                target=management.target,
                                current=response.current,
                                diagnosis=response.diagnosis,
                            ).valid
                    plans[phase.id] = response.recovery_plan

                topology_evidence = phase.evidence.topology
                if topology_evidence is not None:
                    topology = _topology_observation(
                        topology_evidence,
                        phase=phase,
                        store=topology_store,
                    )
                    endpoint_ids = [
                        item.id
                        for item in topology.devices
                        if item.id != topology.root_device_id
                    ]
                    actual.duplicate_entity_ids = len(endpoint_ids) - len(set(endpoint_ids))
                    actual.topology_transitions = [
                        item.kind for item in topology.transitions
                    ]
                    actual.historical_entity_count = len(topology.historical_devices)
                    if topology.transitions:
                        transition = topology.transitions[-1]
                        actual.identity_retained = transition.identity_retained
                        actual.current_attachment = transition.current_interface
                        actual.previous_attachment = transition.previous_interface
                    else:
                        # An endpoint that returns to the port it left produces
                        # no transition, which is the correct reconciliation.
                        # Report its attachment anyway so a scenario can assert
                        # where the endpoint ended up, not merely that nothing
                        # was claimed about it.
                        attached = [
                            item
                            for item in topology.devices
                            if item.id != topology.root_device_id
                            and item.connected_interface
                        ]
                        if len(attached) == 1:
                            actual.current_attachment = attached[0].connected_interface
                            actual.previous_attachment = (
                                attached[0].previous_connected_interface
                            )

                actual.claims = _claims(actual)
                assertions = _assertions(phase.expected, actual)
                phase_results.append(
                    ScenarioPhaseResult(
                        phaseId=phase.id,
                        at=phase.at,
                        transition=phase.transition,
                        status=(
                            "PASS" if all(item.passed for item in assertions) else "FAIL"
                        ),
                        actual=actual,
                        assertions=assertions,
                    )
                )

            # On Windows, release service/store references before the temporary
            # directory is removed so sqlite handles cannot outlive the run.
            del service
            del topology_store
            gc.collect()

        return ScenarioRunResult(
            scenarioId=scenario.id,
            status=(
                "PASS" if all(item.status == "PASS" for item in phase_results) else "FAIL"
            ),
            generatedAt=scenario.phases[-1].at,
            phases=phase_results,
            reasoningModules=self.reasoning_modules,
        )
