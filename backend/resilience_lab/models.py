"""Typed, versioned contracts for SwitchOps Resilience Lab scenarios."""
from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field, model_validator


Confidence = Literal["INDETERMINATE", "LOW", "MEDIUM", "HIGH"]
ResultStatus = Literal["PASS", "FAIL"]


class RouteEvidence(BaseModel):
    destination_prefix: str | None = Field(default="0.0.0.0/0", alias="destinationPrefix")
    next_hop: str | None = Field(default="192.0.2.1", alias="nextHop")
    kind: Literal["connected", "scoped", "default", "none", "unknown"] = "connected"
    route_metric: int | None = Field(default=0, alias="routeMetric")
    protocol: str | None = "NetMgmt"

    model_config = {"populate_by_name": True}


class ManagementEvidence(BaseModel):
    target: str = "192.0.2.10"
    supported: bool = True
    collection_error: str | None = Field(default=None, alias="collectionError")
    adapter_id: str | None = Field(default="adapter-ethernet", alias="adapterId")
    adapter_name: str | None = Field(default="Ethernet", alias="adapterName")
    interface_index: int | None = Field(default=12, alias="interfaceIndex")
    interface_metric: int | None = Field(default=25, alias="interfaceMetric")
    adapter_state: str | None = Field(default="Up", alias="adapterState")
    source_ip: str | None = Field(default="192.0.2.95", alias="sourceIp")
    prefix_length: int | None = Field(default=24, alias="prefixLength")
    connected_prefix: str | None = Field(default="192.0.2.0/24", alias="connectedPrefix")
    target_on_connected_prefix: bool | None = Field(
        default=True, alias="targetOnConnectedPrefix"
    )
    dhcp_enabled: bool | None = Field(default=True, alias="dhcpEnabled")
    dhcp_static_coexistence: bool | None = Field(
        default=False, alias="dhcpStaticCoexistence"
    )
    dhcp_server: str | None = Field(default="192.0.2.1", alias="dhcpServer")
    dhcp_lease_obtained: datetime | None = Field(
        default=None, alias="dhcpLeaseObtained"
    )
    default_gateway: str | None = Field(default="192.0.2.1", alias="defaultGateway")
    route: RouteEvidence = Field(default_factory=RouteEvidence)
    windows_connectivity: str | None = Field(
        default="Internet", alias="windowsConnectivity"
    )
    tcp22: Literal["reachable", "refused", "timed_out", "unreachable", "unavailable"] = (
        "reachable"
    )
    icmp_reachable: bool | None = Field(default=True, alias="icmpReachable")
    session_state: Literal["offline", "connecting", "live", "stale", "reconnecting"] = Field(
        default="live", alias="sessionState"
    )
    session_error_code: str | None = Field(default=None, alias="sessionErrorCode")

    model_config = {"populate_by_name": True}


class MerakiLanScenarioEvidence(BaseModel):
    subnet: str
    vlan_id: str | None = Field(default=None, alias="vlanId")
    appliance_ip: str | None = Field(default=None, alias="applianceIp")
    dhcp_mode: Literal["server", "relay", "disabled", "unknown"] = Field(
        default="unknown", alias="dhcpMode"
    )

    model_config = {"populate_by_name": True}


class MerakiScenarioEvidence(BaseModel):
    state: Literal["not-configured", "healthy", "partial", "unavailable"] = (
        "not-configured"
    )
    freshness: Literal["current", "aging", "stale", "historical"] = "historical"
    complete: bool = False
    detail: str = "Synthetic Meraki evidence is not configured."
    failed_operations: list[str] = Field(default_factory=list, alias="failedOperations")
    lans: list[MerakiLanScenarioEvidence] = Field(default_factory=list)

    model_config = {"populate_by_name": True}


class InterfaceEvidence(BaseModel):
    port: str
    status: str = "connected"
    vlan: str = "1"
    name: str = ""
    speed: str = "a-1000"
    duplex: str = "a-full"


class MacEvidence(BaseModel):
    mac: str
    port: str
    vlan: str = "1"


class AdapterEvidence(BaseModel):
    name: str = "Ethernet"
    ip: str = "192.0.2.95"
    netmask: str = "255.255.255.0"
    mac: str


class TopologyEvidence(BaseModel):
    hostname: str = "switch-doc"
    model: str = "WS-C3560"
    management_ip: str = Field(default="192.0.2.10", alias="managementIp")
    interfaces: list[InterfaceEvidence]
    macs: list[MacEvidence] = Field(default_factory=list)
    adapters: list[AdapterEvidence] = Field(default_factory=list)
    complete: bool = True

    model_config = {"populate_by_name": True}


class PhaseEvidence(BaseModel):
    management: ManagementEvidence | None = None
    meraki: MerakiScenarioEvidence | None = None
    topology: TopologyEvidence | None = None


class PhaseExpectation(BaseModel):
    management_diagnosis: str | None = Field(default=None, alias="managementDiagnosis")
    confidence: Confidence | None = None
    recovery_plan_status: str | None = Field(default=None, alias="recoveryPlanStatus")
    meraki_state: str | None = Field(default=None, alias="merakiState")
    meraki_freshness: str | None = Field(default=None, alias="merakiFreshness")
    last_known_good_observed_at: datetime | None = Field(
        default=None, alias="lastKnownGoodObservedAt"
    )
    binding_from_phase: str | None = Field(default=None, alias="bindingFromPhase")
    binding_valid: bool | None = Field(default=None, alias="bindingValid")
    topology_transition: str | None = Field(default=None, alias="topologyTransition")
    identity_retained: bool | None = Field(default=None, alias="identityRetained")
    duplicate_entity_ids: int | None = Field(default=None, alias="duplicateEntityIds")
    current_attachment: str | None = Field(default=None, alias="currentAttachment")
    previous_attachment: str | None = Field(default=None, alias="previousAttachment")
    historical_entity_count_at_least: int | None = Field(
        default=None, alias="historicalEntityCountAtLeast"
    )
    must_claim: list[str] = Field(default_factory=list, alias="mustClaim")
    may_claim: list[str] = Field(default_factory=list, alias="mayClaim")
    must_not_claim: list[str] = Field(default_factory=list, alias="mustNotClaim")
    writes_performed: Literal[0] = Field(default=0, alias="writesPerformed")

    model_config = {"populate_by_name": True}


class ScenarioPhase(BaseModel):
    id: str = Field(min_length=1, max_length=80)
    at: datetime
    transition: str = Field(min_length=1, max_length=200)
    restart_backend: bool = Field(default=False, alias="restartBackend")
    evidence: PhaseEvidence
    expected: PhaseExpectation

    model_config = {"populate_by_name": True}


class ResilienceScenario(BaseModel):
    schema_version: Literal[1] = Field(default=1, alias="schemaVersion")
    id: str = Field(pattern=r"^[A-Z][A-Z0-9_]{2,79}$")
    description: str = Field(min_length=1, max_length=300)
    purpose: str = Field(min_length=1, max_length=500)
    phases: list[ScenarioPhase] = Field(min_length=1)

    model_config = {"populate_by_name": True}

    @model_validator(mode="after")
    def validate_timeline(self) -> "ResilienceScenario":
        ids = [phase.id for phase in self.phases]
        if len(ids) != len(set(ids)):
            raise ValueError("Scenario phase IDs must be unique.")
        times = [phase.at for phase in self.phases]
        if any(value.tzinfo is None for value in times):
            raise ValueError("Scenario timestamps must be timezone-aware.")
        if any(current <= previous for previous, current in zip(times, times[1:])):
            raise ValueError("Scenario phases must have strictly increasing timestamps.")
        return self


class ScenarioCatalog(BaseModel):
    schema_version: Literal[1] = Field(default=1, alias="schemaVersion")
    scenarios: list[ResilienceScenario]

    model_config = {"populate_by_name": True}

    @model_validator(mode="after")
    def validate_ids(self) -> "ScenarioCatalog":
        ids = [scenario.id for scenario in self.scenarios]
        if len(ids) != len(set(ids)):
            raise ValueError("Scenario IDs must be unique.")
        return self


class AssertionResult(BaseModel):
    dimension: Literal[
        "classification",
        "identity-retention",
        "topology-reconciliation",
        "recovery-planning",
        "unsafe-action-prevention",
        "historical-continuity",
    ]
    expectation: str
    actual: str
    passed: bool


class ActualPhaseOutcome(BaseModel):
    management_diagnosis: str | None = Field(default=None, alias="managementDiagnosis")
    confidence: str | None = None
    recovery_plan_status: str | None = Field(default=None, alias="recoveryPlanStatus")
    recovery_blockers: list[str] = Field(default_factory=list, alias="recoveryBlockers")
    meraki_state: str | None = Field(default=None, alias="merakiState")
    meraki_freshness: str | None = Field(default=None, alias="merakiFreshness")
    last_known_good_observed_at: datetime | None = Field(
        default=None, alias="lastKnownGoodObservedAt"
    )
    binding_valid: bool | None = Field(default=None, alias="bindingValid")
    topology_transitions: list[str] = Field(default_factory=list, alias="topologyTransitions")
    identity_retained: bool | None = Field(default=None, alias="identityRetained")
    duplicate_entity_ids: int = Field(default=0, alias="duplicateEntityIds")
    current_attachment: str | None = Field(default=None, alias="currentAttachment")
    previous_attachment: str | None = Field(default=None, alias="previousAttachment")
    historical_entity_count: int = Field(default=0, alias="historicalEntityCount")
    claims: list[str] = Field(default_factory=list)
    writes_performed: int = Field(default=0, alias="writesPerformed")

    model_config = {"populate_by_name": True}


class ScenarioPhaseResult(BaseModel):
    phase_id: str = Field(alias="phaseId")
    at: datetime
    transition: str
    status: ResultStatus
    actual: ActualPhaseOutcome
    assertions: list[AssertionResult]

    model_config = {"populate_by_name": True}


class ScenarioRunResult(BaseModel):
    scenario_id: str = Field(alias="scenarioId")
    status: ResultStatus
    generated_at: datetime = Field(alias="generatedAt")
    phases: list[ScenarioPhaseResult]
    reasoning_modules: list[str] = Field(alias="reasoningModules")

    model_config = {"populate_by_name": True}
