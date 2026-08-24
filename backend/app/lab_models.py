"""Typed, provider-neutral contracts for v0.8 Lab Assurance.

The contracts deliberately keep three ideas separate:

* what was observed and by which command;
* what SwitchOps can safely conclude from that evidence; and
* what remains unknown.

No field is a generic health score and no capability is inferred from a vendor
name alone.
"""
from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field, field_validator


CapabilityState = Literal["SUPPORTED", "UNSUPPORTED", "UNKNOWN"]
EvidenceConfidence = Literal["CONFIRMED", "HIGH", "UNKNOWN"]
HopState = Literal["PROVEN", "INFERRED", "EXPECTED", "AMBIGUOUS", "UNKNOWN"]
FindingSeverity = Literal["CRITICAL", "WARNING", "NOTICE", "UNKNOWN"]
ProbeState = Literal["HEALTHY", "DEGRADED", "UNREACHABLE", "INSUFFICIENT_EVIDENCE"]


class LabEvidence(BaseModel):
    id: str
    device_id: str = Field(alias="deviceId")
    kind: str
    command: str
    confidence: EvidenceConfidence
    observed_at: datetime = Field(alias="observedAt")
    current: bool = True
    detail: str

    model_config = {"populate_by_name": True}


class LabCapability(BaseModel):
    id: str
    device_id: str = Field(alias="deviceId")
    name: str
    state: CapabilityState
    configured: bool | None = None
    observed: bool | None = None
    detail: str
    evidence_ids: list[str] = Field(default_factory=list, alias="evidenceIds")

    model_config = {"populate_by_name": True}


class LabInterface(BaseModel):
    id: str
    device_id: str = Field(alias="deviceId")
    name: str
    admin_state: Literal["UP", "DOWN", "UNKNOWN"] = Field(alias="adminState")
    oper_state: Literal["UP", "DOWN", "UNKNOWN"] = Field(alias="operState")
    mode: Literal["ACCESS", "TRUNK", "ROUTED", "DYNAMIC", "UNKNOWN"] = "UNKNOWN"
    access_vlan: int | None = Field(default=None, alias="accessVlan")
    native_vlan: int | None = Field(default=None, alias="nativeVlan")
    allowed_vlans: list[int] = Field(default_factory=list, alias="allowedVlans")
    speed_mbps: int | None = Field(default=None, alias="speedMbps")
    description: str | None = None
    port_channel: str | None = Field(default=None, alias="portChannel")
    poe_watts: float | None = Field(default=None, alias="poeWatts")
    learned_mac_count: int = Field(default=0, alias="learnedMacCount")
    error_count: int = Field(default=0, alias="errorCount")
    drop_count: int = Field(default=0, alias="dropCount")
    input_bps: int | None = Field(default=None, alias="inputBps")
    output_bps: int | None = Field(default=None, alias="outputBps")
    utilization_percent: float | None = Field(default=None, alias="utilizationPercent")
    evidence_ids: list[str] = Field(default_factory=list, alias="evidenceIds")

    model_config = {"populate_by_name": True}


class LabDevice(BaseModel):
    id: str
    label: str
    role: Literal["SWITCH", "ROUTER", "GATEWAY", "ACCESS_POINT", "ENDPOINT", "UNKNOWN"]
    provider: Literal["cisco-ios", "local-probe"] = "cisco-ios"
    model: str | None = None
    software: str | None = None
    primary: bool = False
    observed: bool = True
    collection_state: Literal["CURRENT", "PARTIAL", "FAILED", "NOT_COLLECTED"] = Field(
        default="CURRENT", alias="collectionState"
    )
    detail: str = ""
    evidence_ids: list[str] = Field(default_factory=list, alias="evidenceIds")

    model_config = {"populate_by_name": True}


class LabEdge(BaseModel):
    id: str
    from_node_id: str = Field(alias="fromNodeId")
    to_node_id: str = Field(alias="toNodeId")
    from_interface: str | None = Field(default=None, alias="fromInterface")
    to_interface: str | None = Field(default=None, alias="toInterface")
    kind: Literal[
        "PHYSICAL",
        "PORT_CHANNEL_MEMBER",
        "L2_MEMBERSHIP",
        "L3_GATEWAY",
        "ROUTING_ADJACENCY",
        "EXPECTED",
    ]
    state: HopState
    confidence: EvidenceConfidence
    reciprocal: bool = False
    detail: str
    evidence_ids: list[str] = Field(default_factory=list, alias="evidenceIds")

    model_config = {"populate_by_name": True}


class LogicalNetwork(BaseModel):
    id: str
    vlan_id: int | None = Field(default=None, alias="vlanId")
    name: str
    vrf: str | None = None
    gateway_nodes: list[str] = Field(default_factory=list, alias="gatewayNodes")
    member_interfaces: list[str] = Field(default_factory=list, alias="memberInterfaces")
    trunk_interfaces: list[str] = Field(default_factory=list, alias="trunkInterfaces")
    endpoint_nodes: list[str] = Field(default_factory=list, alias="endpointNodes")
    isolation_state: Literal["PROVEN", "POLICY_UNKNOWN", "NOT_ISOLATED", "UNKNOWN"] = Field(
        default="POLICY_UNKNOWN", alias="isolationState"
    )
    detail: str
    evidence_ids: list[str] = Field(default_factory=list, alias="evidenceIds")

    model_config = {"populate_by_name": True}


class LabFinding(BaseModel):
    id: str
    category: Literal[
        "RESILIENCY",
        "LAYER2",
        "SEGMENTATION",
        "SECURITY",
        "CAPACITY",
        "PERFORMANCE",
        "EVIDENCE",
    ]
    severity: FindingSeverity
    confidence: EvidenceConfidence
    title: str
    detail: str
    consequence: str
    remediation: str | None = None
    affected_ids: list[str] = Field(default_factory=list, alias="affectedIds")
    evidence_ids: list[str] = Field(default_factory=list, alias="evidenceIds")

    model_config = {"populate_by_name": True}


class FailureScenario(BaseModel):
    id: str
    target_id: str = Field(alias="targetId")
    target_kind: Literal[
        "INTERFACE", "UPLINK", "SWITCH", "GATEWAY", "PORT_CHANNEL_MEMBER", "ACCESS_POINT", "POE", "ADJACENCY"
    ] = Field(alias="targetKind")
    title: str
    confidence: EvidenceConfidence
    consequences: list[str]
    affected_ids: list[str] = Field(default_factory=list, alias="affectedIds")
    control_impact: str = Field(alias="controlImpact")
    evidence_ids: list[str] = Field(default_factory=list, alias="evidenceIds")

    model_config = {"populate_by_name": True}


class PathHop(BaseModel):
    node_id: str = Field(alias="nodeId")
    label: str
    via_interface: str | None = Field(default=None, alias="viaInterface")
    state: HopState
    evidence_ids: list[str] = Field(default_factory=list, alias="evidenceIds")

    model_config = {"populate_by_name": True}


class LabPath(BaseModel):
    id: str
    from_node_id: str = Field(alias="fromNodeId")
    to_node_id: str = Field(alias="toNodeId")
    state: HopState
    summary: str
    hops: list[PathHop]
    evidence_ids: list[str] = Field(default_factory=list, alias="evidenceIds")

    model_config = {"populate_by_name": True}


class PerformanceObservation(BaseModel):
    id: str
    target_label: str = Field(alias="targetLabel")
    target_token: str = Field(alias="targetToken")
    state: ProbeState
    observed_at: datetime = Field(alias="observedAt")
    transmitted: int
    received: int
    loss_percent: float | None = Field(default=None, alias="lossPercent")
    latency_avg_ms: float | None = Field(default=None, alias="latencyAvgMs")
    jitter_ms: float | None = Field(default=None, alias="jitterMs")
    route_changed: bool | None = Field(default=None, alias="routeChanged")
    detail: str

    model_config = {"populate_by_name": True}


class LabAssuranceSummary(BaseModel):
    observed_devices: int = Field(alias="observedDevices")
    physical_edges: int = Field(alias="physicalEdges")
    logical_networks: int = Field(alias="logicalNetworks")
    critical_findings: int = Field(alias="criticalFindings")
    warning_findings: int = Field(alias="warningFindings")
    unknown_findings: int = Field(alias="unknownFindings")
    evidence_gaps: int = Field(alias="evidenceGaps")

    model_config = {"populate_by_name": True}


class LabAssuranceState(BaseModel):
    generated_at: datetime = Field(alias="generatedAt")
    collection_state: Literal["NOT_COLLECTED", "CURRENT", "PARTIAL", "FAILED"] = Field(
        alias="collectionState"
    )
    summary: LabAssuranceSummary
    devices: list[LabDevice] = Field(default_factory=list)
    interfaces: list[LabInterface] = Field(default_factory=list)
    edges: list[LabEdge] = Field(default_factory=list)
    logical_networks: list[LogicalNetwork] = Field(default_factory=list, alias="logicalNetworks")
    capabilities: list[LabCapability] = Field(default_factory=list)
    findings: list[LabFinding] = Field(default_factory=list)
    failures: list[FailureScenario] = Field(default_factory=list)
    paths: list[LabPath] = Field(default_factory=list)
    performance: list[PerformanceObservation] = Field(default_factory=list)
    evidence: list[LabEvidence] = Field(default_factory=list)
    limitations: list[str] = Field(default_factory=list)

    model_config = {"populate_by_name": True}


class LabDeviceCreateRequest(BaseModel):
    label: str = Field(min_length=1, max_length=80)
    host: str = Field(min_length=1, max_length=253)
    username: str = Field(min_length=1, max_length=128)
    password: str = Field(min_length=1, max_length=512)
    enable_secret: str = Field(default="", max_length=512, alias="enableSecret")
    device_type: Literal["cisco_ios", "cisco_xe"] = Field(default="cisco_ios", alias="deviceType")

    @field_validator("label", "host", "username")
    @classmethod
    def safe_text(cls, value: str) -> str:
        value = value.strip()
        if not value or any(ord(char) < 32 or ord(char) == 127 for char in value):
            raise ValueError("Value contains invalid characters.")
        return value

    model_config = {"populate_by_name": True}


class ConfiguredLabDevice(BaseModel):
    id: str
    label: str
    primary: bool = False
    device_type: str = Field(alias="deviceType")
    storage: Literal["keyring", "legacy", "none"]
    configured: bool

    model_config = {"populate_by_name": True}


class LabDeviceList(BaseModel):
    keyring_available: bool = Field(alias="keyringAvailable")
    devices: list[ConfiguredLabDevice]

    model_config = {"populate_by_name": True}


class LabRefreshResult(BaseModel):
    accepted: bool
    state: LabAssuranceState


class PerformanceProbeRequest(BaseModel):
    target: str = Field(min_length=1, max_length=253)
    label: str = Field(min_length=1, max_length=80)
    count: int = Field(default=4, ge=1, le=5)

    @field_validator("target", "label")
    @classmethod
    def safe_probe_text(cls, value: str) -> str:
        value = value.strip()
        if not value or any(ord(char) < 32 or ord(char) == 127 for char in value):
            raise ValueError("Value contains invalid characters.")
        return value

    model_config = {"populate_by_name": True}
