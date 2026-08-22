"""Pydantic models for the SwitchOps API."""
from __future__ import annotations

from datetime import datetime
import re
from typing import Any, Dict, List, Literal, Optional

from pydantic import BaseModel, Field, field_validator


# --- Setup / credentials ---------------------------------------------------

class SetupStatus(BaseModel):
    configured: bool
    has_password: bool = Field(alias="hasPassword")
    has_enable_secret: bool = Field(alias="hasEnableSecret")
    storage: str  # "keyring" | "file" | "env" | "none"
    mock_mode: bool = Field(alias="mockMode")
    enable_write_actions: bool = Field(alias="enableWriteActions")
    switch_host: Optional[str] = Field(default=None, alias="switchHost")
    switch_username: Optional[str] = Field(default=None, alias="switchUsername")
    switch_device_type: Optional[str] = Field(default=None, alias="switchDeviceType")

    model_config = {"populate_by_name": True}


class MockScenarioRequest(BaseModel):
    scenario: Literal["baseline", "ap_attached"]


class MockScenarioStatus(BaseModel):
    scenario: Literal["baseline", "ap_attached"]
    mock_mode: bool = Field(alias="mockMode")

    model_config = {"populate_by_name": True}


class CredentialSetupRequest(BaseModel):
    switch_host: str = Field(alias="switchHost")
    switch_username: str = Field(alias="switchUsername")
    switch_password: str = Field(alias="switchPassword")
    switch_enable_secret: Optional[str] = Field(default=None, alias="switchEnableSecret")
    switch_device_type: Literal["cisco_ios"] = Field(default="cisco_ios", alias="switchDeviceType")

    model_config = {"populate_by_name": True}

    @field_validator("switch_host")
    @classmethod
    def validate_switch_host(cls, value: str) -> str:
        value = value.strip()
        if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._:-]{0,252}", value):
            raise ValueError("Switch host must be an IP address or hostname without control characters.")
        return value

    @field_validator("switch_username")
    @classmethod
    def validate_switch_username(cls, value: str) -> str:
        value = value.strip()
        if not value or len(value) > 128 or any(ord(char) < 32 for char in value):
            raise ValueError("Switch username is invalid.")
        return value

    @field_validator("switch_password", "switch_enable_secret")
    @classmethod
    def validate_secret_length(cls, value: Optional[str]) -> Optional[str]:
        if value is not None and len(value) > 1024:
            raise ValueError("Credential value is too long.")
        return value


# --- Runtime information ---------------------------------------------------


class RuntimeInfo(BaseModel):
    """Non-secret facts about how this backend is running.

    Everything here is local configuration the operator already controls. No
    credential, secret, or credential-file content is included.
    """

    version: str
    api_host: str = Field(alias="apiHost")
    api_port: int = Field(alias="apiPort")
    mock_mode: bool = Field(alias="mockMode")
    enable_write_actions: bool = Field(alias="enableWriteActions")
    legacy_ssh: bool = Field(alias="legacySsh")
    api_docs_enabled: bool = Field(alias="apiDocsEnabled")
    host_key_pinned: bool = Field(alias="hostKeyPinned")
    telemetry_retention_days: int = Field(alias="telemetryRetentionDays")
    telemetry_collection: Literal["refresh-driven"] = Field(
        default="refresh-driven", alias="telemetryCollection"
    )
    data_dir: str = Field(alias="dataDir")
    backup_dir: str = Field(alias="backupDir")
    log_dir: str = Field(alias="logDir")
    cors_origins: List[str] = Field(default_factory=list, alias="corsOrigins")
    device_driver: Optional[str] = Field(default=None, alias="deviceDriver")

    model_config = {"populate_by_name": True}


# --- Connection diagnostics ------------------------------------------------

ConnectionCheckStatus = Literal["pass", "fail", "skipped"]


class ConnectionCheck(BaseModel):
    id: str
    label: str
    status: ConnectionCheckStatus
    detail: str


class ConnectionTestResult(BaseModel):
    """Result of a bounded read-only reachability test.

    ``failure_code`` is a fixed vocabulary, never a server exception string, so
    no credential, path, or internal detail can escape through this response.
    """

    ok: bool
    mode: Literal["mock", "real"]
    summary: str
    checks: List[ConnectionCheck] = Field(default_factory=list)
    failure_code: Optional[str] = Field(default=None, alias="failureCode")
    tested_at: datetime = Field(alias="testedAt")
    duration_ms: int = Field(default=0, alias="durationMs")

    model_config = {"populate_by_name": True}


# --- Common ----------------------------------------------------------------

class CommandResult(BaseModel):
    command: str
    success: bool
    duration_ms: int = Field(alias="durationMs")
    output: str

    model_config = {"populate_by_name": True}


class ApiError(BaseModel):
    code: str
    message: str
    detail: Optional[str] = None


# --- Switch domain ---------------------------------------------------------

HealthState = Literal["HEALTHY", "NOTICE", "ATTENTION", "CRITICAL"]


class HealthReason(BaseModel):
    code: str
    severity: HealthState
    title: str
    detail: str
    interface: Optional[str] = None


class HealthAssessment(BaseModel):
    state: HealthState = "HEALTHY"
    reasons: List[HealthReason] = Field(default_factory=list)
    evaluated_at: datetime = Field(alias="evaluatedAt")
    based_on_history: bool = Field(default=False, alias="basedOnHistory")

    model_config = {"populate_by_name": True}


class InterfaceDelta(BaseModel):
    port: str
    previous_total_errors: Optional[int] = Field(default=None, alias="previousTotalErrors")
    current_total_errors: int = Field(default=0, alias="currentTotalErrors")
    error_delta: Optional[int] = Field(default=None, alias="errorDelta")
    counter_state: Literal["first", "unchanged", "increased", "reset", "wrapped"] = Field(
        default="first", alias="counterState"
    )
    status_before: Optional[str] = Field(default=None, alias="statusBefore")
    status_after: str = Field(default="", alias="statusAfter")
    admin_before: Optional[str] = Field(default=None, alias="adminBefore")
    admin_after: str = Field(default="unknown", alias="adminAfter")
    speed_before: Optional[str] = Field(default=None, alias="speedBefore")
    speed_after: str = Field(default="", alias="speedAfter")
    duplex_before: Optional[str] = Field(default=None, alias="duplexBefore")
    duplex_after: str = Field(default="", alias="duplexAfter")
    vlan_before: Optional[str] = Field(default=None, alias="vlanBefore")
    vlan_after: str = Field(default="", alias="vlanAfter")
    poe_before: Optional[str] = Field(default=None, alias="poeBefore")
    poe_after: str = Field(default="", alias="poeAfter")

    model_config = {"populate_by_name": True}


class TelemetrySnapshotSummary(BaseModel):
    observed_at: datetime = Field(alias="observedAt")
    previous_observed_at: Optional[datetime] = Field(default=None, alias="previousObservedAt")
    history_available: bool = Field(default=False, alias="historyAvailable")
    interface_deltas: List[InterfaceDelta] = Field(default_factory=list, alias="interfaceDeltas")
    retention_days: int = Field(default=30, alias="retentionDays")

    model_config = {"populate_by_name": True}


class DeviceObservationPoint(BaseModel):
    timestamp: datetime
    reachable: bool
    cpu_5sec: Optional[float] = Field(default=None, alias="cpu5Sec")
    memory_used_pct: Optional[float] = Field(default=None, alias="memoryUsedPct")
    temperature_c: Optional[int] = Field(default=None, alias="temperatureC")
    poe_used_w: Optional[float] = Field(default=None, alias="poeUsedW")
    poe_available_w: Optional[float] = Field(default=None, alias="poeAvailableW")

    model_config = {"populate_by_name": True}


class TelemetryHistoryResponse(BaseModel):
    device_id: str = Field(alias="deviceId")
    observations: List[DeviceObservationPoint]

    model_config = {"populate_by_name": True}


class NetworkEvent(BaseModel):
    id: Optional[int] = None
    timestamp: datetime
    device_id: str = Field(alias="deviceId")
    interface: Optional[str] = None
    event_type: str = Field(alias="eventType")
    severity: HealthState
    title: str
    detail: str
    metadata: Dict[str, Any] = Field(default_factory=dict)

    model_config = {"populate_by_name": True}


class NetworkEventsResponse(BaseModel):
    events: List[NetworkEvent]


DeviceType = Literal[
    "router",
    "switch",
    "access-point",
    "desktop",
    "laptop",
    "server",
    "phone",
    "tv-media",
    "printer",
    "camera",
    "unknown",
]


# How strongly the observation supports a claim about what sits on a link.
#
# direct            the neighbour announced itself on the wire (CDP/LLDP)
# observed-on-port  something is demonstrably attached (link up + learned MACs)
#                   but the switch cannot prove it is the *only* hop
# learned-behind    a MAC reachable through this interface, possibly several
#                   devices away; never proof of physical attachment
# expected          inferred from an interface description; nothing observed
# unknown           insufficient evidence
EvidenceLevel = Literal[
    "direct",
    "observed-on-port",
    "learned-behind",
    "expected",
    "unknown",
]

# Where a device's *identity* (not its existence) came from. Existence and
# identity are separate claims: a link can be certain while the thing on the
# end of it is only a label somebody typed into a description.
IdentitySource = Literal[
    "cdp",
    "lldp",
    "interface-description",
    "mac-oui",
    "user-intent",
    "meraki-api",
    "historical",
    "switch-telemetry",
    "none",
]

InterfaceRole = Literal["uplink", "access", "unknown"]


# --- Topology reconciliation ------------------------------------------------
#
# SwitchOps holds several claims about the same interface at the same time and
# reconciles them, rather than letting the newest or the prettiest overwrite
# the others. The classes below are the vocabulary for that.

# What *kind* of knowledge a claim is. This is the axis that stops configured
# intent from being rendered as observed truth.
#
# observed    proven by current telemetry from a device
# expected    believed to be intended (description, user intent, accepted plan)
# historical  observed in an earlier snapshot, not now
# inferred    supported by evidence but not directly proven
# unknown     insufficient evidence
EvidenceClass = Literal["observed", "expected", "historical", "inferred", "unknown"]

# Which concrete source produced a claim.
EvidenceSource = Literal[
    "cdp",
    "lldp",
    "mac-table",
    "arp",
    "interface-telemetry",
    "interface-description",
    "user-intent",
    "accepted-plan",
    "prior-observation",
    "mac-address-form",
    "meraki-api",
    "none",
]

# What the claim says about the relationship between an interface and whatever
# is on the other side of it.
RelationshipKind = Literal[
    # the neighbour announced itself on the wire; proves direct attachment
    "direct-neighbour",
    # link is up and addresses are being learned, so something is attached -
    # but it may itself be a switch, router or AP with more behind it
    "attached-endpoint",
    # reachable through this interface, possibly several hops away
    "learned-behind",
    # this switch's default gateway is reachable through this interface
    "gateway-path",
    # intent only; nothing observed
    "expected-neighbour",
]

Confidence = Literal["low", "medium", "high"]


class TopologyAssertion(BaseModel):
    """One claim, by one source, about one interface, at one time.

    Assertions are additive. Two sources are allowed to disagree; resolving
    that disagreement is the reconciler's job, not the collector's.
    """

    subject: str  # the local interface, e.g. "Gi0/1"
    relationship: RelationshipKind
    # Human label for whatever is on the other end. When ``object_identified``
    # is False this is a placeholder such as "Unidentified device" and must
    # never be treated as a name.
    object_label: str = Field(alias="objectLabel")
    object_identified: bool = Field(default=False, alias="objectIdentified")
    evidence_class: EvidenceClass = Field(alias="evidenceClass")
    source: EvidenceSource
    confidence: Confidence
    detail: str
    observed_at: Optional[datetime] = Field(default=None, alias="observedAt")
    # Optional structured identity, only populated by identifying sources.
    device_type: Optional[DeviceType] = Field(default=None, alias="deviceType")
    vendor: Optional[str] = None
    model: Optional[str] = None

    model_config = {"populate_by_name": True}


class ExternalSighting(BaseModel):
    """A device observed by some source *other* than this switch.

    This is the seam a Meraki controller plugs into. When an expected device is
    absent from its expected interface but another source can see it elsewhere,
    the situation is location drift rather than a missing device. SwitchOps
    never fabricates one of these; with no external provider configured the
    set is simply empty.
    """

    label: str
    observed_location: str = Field(alias="observedLocation")
    source: EvidenceSource = "meraki-api"
    confidence: Confidence = "medium"
    observed_at: Optional[datetime] = Field(default=None, alias="observedAt")
    detail: str = ""

    model_config = {"populate_by_name": True}


# The outcome of comparing observed / expected / historical for one interface.
#
# aligned                 expected and observed agree
# drift                   both exist and disagree
# expected-not-observed   intent exists, nothing is observed there now
# unexpected              something is observed that no intent accounts for
# uncertain               evidence cannot confirm or refute the expectation
# not-applicable          no intent and nothing observed (idle spare port)
ReconciliationStatus = Literal[
    "aligned",
    "drift",
    "expected-not-observed",
    "unexpected",
    "uncertain",
    "not-applicable",
]

# Drift is not one thing. Identity drift means the wrong device is here;
# location drift means the right device is somewhere else, which needs an
# evidence source beyond this switch to establish.
DriftKind = Literal["identity", "location", "none"]


class InterfaceReconciliation(BaseModel):
    interface: str
    status: ReconciliationStatus
    drift_kind: DriftKind = Field(default="none", alias="driftKind")
    headline: str
    explanation: str
    observed: Optional[TopologyAssertion] = None
    expected: Optional[TopologyAssertion] = None
    historical: Optional[TopologyAssertion] = None
    inferred: List[TopologyAssertion] = Field(default_factory=list)
    # Whether the *observation* differs from the previous observation. This is
    # orthogonal to status: a link can be aligned and still have changed.
    changed_since_previous: bool = Field(default=False, alias="changedSincePrevious")
    change_summary: Optional[str] = Field(default=None, alias="changeSummary")
    # Every assertion held about this interface, for the inspector.
    assertions: List[TopologyAssertion] = Field(default_factory=list)
    # True when the interface description no longer matches the active intent,
    # i.e. the switch's own documentation is stale.
    documentation_stale: bool = Field(default=False, alias="documentationStale")

    model_config = {"populate_by_name": True}


class ReconciliationSummary(BaseModel):
    """Whole-device reconciliation. Deliberately separate from health."""

    evaluated_at: datetime = Field(alias="evaluatedAt")
    device_id: str = Field(alias="deviceId")
    aligned: int = 0
    drift: int = 0
    expected_not_observed: int = Field(default=0, alias="expectedNotObserved")
    unexpected: int = 0
    uncertain: int = 0
    changed: int = 0
    # True when at least one interface needs a human decision. Never implies
    # the network is unhealthy.
    attention: bool = False
    headline: str = "No topology intent recorded yet."
    interfaces: List[InterfaceReconciliation] = Field(default_factory=list)

    model_config = {"populate_by_name": True}


# --- Expected topology (SwitchOps-local intent) -----------------------------

# Ranked by authority. A user statement beats an accepted plan, which beats a
# description somebody typed into the switch years ago.
IntentSource = Literal["user-intent", "accepted-plan", "interface-description"]


class ExpectedRelationship(BaseModel):
    """What the operator says should be on an interface.

    Stored locally by SwitchOps. Writing one of these never touches the switch.
    """

    device_id: str = Field(alias="deviceId")
    interface: str
    expected_name: str = Field(alias="expectedName")
    expected_device_type: DeviceType = Field(default="unknown", alias="expectedDeviceType")
    expected_vendor: Optional[str] = Field(default=None, alias="expectedVendor")
    expected_model: Optional[str] = Field(default=None, alias="expectedModel")
    source: IntentSource = "user-intent"
    note: Optional[str] = None
    # Muted: the operator has said this interface is not worth reconciling.
    # The discrepancy is not deleted, it simply stops asking for attention.
    suppressed: bool = False
    created_at: Optional[datetime] = Field(default=None, alias="createdAt")
    updated_at: Optional[datetime] = Field(default=None, alias="updatedAt")

    model_config = {"populate_by_name": True}


class ExpectedRelationshipRequest(BaseModel):
    expected_name: str = Field(alias="expectedName", min_length=1, max_length=64)
    expected_device_type: DeviceType = Field(default="unknown", alias="expectedDeviceType")
    expected_vendor: Optional[str] = Field(default=None, alias="expectedVendor", max_length=64)
    expected_model: Optional[str] = Field(default=None, alias="expectedModel", max_length=64)
    note: Optional[str] = Field(default=None, max_length=200)
    suppressed: bool = False

    model_config = {"populate_by_name": True}

    @field_validator("expected_name", "expected_vendor", "expected_model", "note")
    @classmethod
    def validate_text(cls, value: Optional[str]) -> Optional[str]:
        if value is None:
            return None
        value = value.strip()
        if any(ord(char) < 32 for char in value):
            raise ValueError("Value contains control characters.")
        return value or None


class ExpectedTopologyResponse(BaseModel):
    device_id: str = Field(alias="deviceId")
    relationships: List[ExpectedRelationship] = Field(default_factory=list)

    model_config = {"populate_by_name": True}


class CdpNeighbor(BaseModel):
    remote_name: str = Field(alias="remoteName")
    local_interface: str = Field(default="", alias="localInterface")
    remote_interface: Optional[str] = Field(default=None, alias="remoteInterface")
    platform: Optional[str] = None
    capabilities: List[str] = Field(default_factory=list)
    ip: Optional[str] = None

    model_config = {"populate_by_name": True}


class DeviceCapability(BaseModel):
    name: str
    available: bool = True
    source: str


class NetworkDevice(BaseModel):
    id: str
    type: DeviceType
    vendor: Optional[str] = None
    model: Optional[str] = None
    name: str
    mac: Optional[str] = None
    ip: Optional[str] = None
    source: Literal["observed", "inferred", "expected"]
    confidence: Literal["low", "medium", "high"]
    classification_stage: Literal["unknown", "category", "vendor", "model"] = Field(
        alias="classificationStage"
    )
    online: bool
    connected_interface: Optional[str] = Field(default=None, alias="connectedInterface")
    visual_category: DeviceType = Field(alias="visualCategory")
    capabilities: List[DeviceCapability] = Field(default_factory=list)
    last_seen: Optional[datetime] = Field(default=None, alias="lastSeen")
    evidence: List[str] = Field(default_factory=list)
    evidence_level: EvidenceLevel = Field(default="unknown", alias="evidenceLevel")
    identity_source: IdentitySource = Field(default="none", alias="identitySource")
    # What intent says should be here, kept separate from ``name`` so an
    # observed node can never borrow a description for its identity.
    expected_name: Optional[str] = Field(default=None, alias="expectedName")
    expected_type: Optional[DeviceType] = Field(default=None, alias="expectedType")
    # Addresses reachable through the link to this device. More than one means
    # further devices sit behind it; it never multiplies this device.
    learned_mac_count: int = Field(default=0, alias="learnedMacCount")
    role: InterfaceRole = "unknown"

    model_config = {"populate_by_name": True}


class NetworkInterface(BaseModel):
    id: str
    device_id: str = Field(alias="deviceId")
    port: str
    description: str = ""
    admin_state: Literal["up", "down", "unknown"] = Field(alias="adminState")
    oper_state: Literal["up", "down", "unknown"] = Field(alias="operState")
    speed: str = ""
    duplex: str = ""
    vlan: str = ""
    poe_capable: bool = Field(default=False, alias="poeCapable")
    poe_state: str = Field(default="", alias="poeState")
    poe_watts: float = Field(default=0.0, alias="poeWatts")
    protected: bool = False
    role: InterfaceRole = "unknown"
    learned_mac_count: int = Field(default=0, alias="learnedMacCount")

    model_config = {"populate_by_name": True}


class NetworkLink(BaseModel):
    id: str
    from_device_id: str = Field(alias="fromDeviceId")
    from_interface: str = Field(alias="fromInterface")
    to_device_id: str = Field(alias="toDeviceId")
    to_interface: Optional[str] = Field(default=None, alias="toInterface")
    status: Literal["up", "down", "waiting", "unknown"]
    speed: str = ""
    poe: bool = False
    confidence: Literal["low", "medium", "high"]
    evidence: List[str] = Field(default_factory=list)
    evidence_level: EvidenceLevel = Field(default="unknown", alias="evidenceLevel")
    learned_mac_count: int = Field(default=0, alias="learnedMacCount")

    model_config = {"populate_by_name": True}


class TopologyModel(BaseModel):
    generated_at: datetime = Field(alias="generatedAt")
    root_device_id: str = Field(alias="rootDeviceId")
    devices: List[NetworkDevice]
    interfaces: List[NetworkInterface]
    links: List[NetworkLink]

    model_config = {"populate_by_name": True}

class SwitchSummary(BaseModel):
    hostname: str
    model: str
    management_ip: str = Field(alias="managementIp")
    gateway: str
    ios_version: str = Field(alias="iosVersion")
    serial: Optional[str] = None
    pid: Optional[str] = None
    hardware_revision: Optional[str] = Field(default=None, alias="hardwareRevision")
    ios_image: Optional[str] = Field(default=None, alias="iosImage")
    bootloader: Optional[str] = None
    interface_counts: Optional[str] = Field(default=None, alias="interfaceCounts")
    uptime: Optional[str] = None
    temperature_c: Optional[int] = Field(default=None, alias="temperatureC")
    temperature_state: str = Field(default="UNKNOWN", alias="temperatureState")
    cpu_5sec: Optional[float] = Field(default=None, alias="cpu5Sec")
    poe_available_w: float = Field(default=0.0, alias="poeAvailableW")
    poe_used_w: float = Field(default=0.0, alias="poeUsedW")
    connected_ports: List[str] = Field(default_factory=list, alias="connectedPorts")
    shutdown_ports: List[str] = Field(default_factory=list, alias="shutdownPorts")
    error_ports: List[str] = Field(default_factory=list, alias="errorPorts")
    summary: str = "Switch state pending."
    healthy: bool = True
    health: HealthAssessment
    telemetry_complete: bool = Field(default=True, alias="telemetryComplete")

    model_config = {"populate_by_name": True}


class InterfaceStatus(BaseModel):
    port: str
    name: str = ""
    status: str = ""
    vlan: str = ""
    duplex: str = ""
    speed: str = ""
    type: str = ""
    protected: bool = False
    notes: Optional[str] = None


class InterfaceStatusResponse(BaseModel):
    interfaces: List[InterfaceStatus]


class InterfaceErrorCounters(BaseModel):
    port: str
    align_err: int = Field(default=0, alias="alignErr")
    fcs_err: int = Field(default=0, alias="fcsErr")
    xmit_err: int = Field(default=0, alias="xmitErr")
    rcv_err: int = Field(default=0, alias="rcvErr")
    under_size: int = Field(default=0, alias="underSize")
    single_col: int = Field(default=0, alias="singleCol")
    multi_col: int = Field(default=0, alias="multiCol")
    late_col: int = Field(default=0, alias="lateCol")
    excess_col: int = Field(default=0, alias="excessCol")
    total: int = 0

    model_config = {"populate_by_name": True}


class InterfaceErrorsResponse(BaseModel):
    counters: List[InterfaceErrorCounters]
    total_errors: int = Field(alias="totalErrors")
    healthy: bool

    model_config = {"populate_by_name": True}


class PoePort(BaseModel):
    interface: str
    admin: str = ""
    oper: str = ""
    power_watts: float = Field(default=0.0, alias="powerWatts")
    device: str = "n/a"
    poe_class: str = Field(default="n/a", alias="class")
    max_watts: float = Field(default=30.0, alias="maxWatts")

    model_config = {"populate_by_name": True}


class PoeResponse(BaseModel):
    available_watts: float = Field(alias="availableWatts")
    used_watts: float = Field(alias="usedWatts")
    remaining_watts: float = Field(alias="remainingWatts")
    ports: List[PoePort]

    model_config = {"populate_by_name": True}


class EnvironmentStatus(BaseModel):
    temperature_c: Optional[int] = Field(default=None, alias="temperatureC")
    state: str = "UNKNOWN"
    yellow_threshold_c: Optional[int] = Field(default=None, alias="yellowThresholdC")
    red_threshold_c: Optional[int] = Field(default=None, alias="redThresholdC")
    power_status: str = Field(default="unknown", alias="powerStatus")
    raw: Optional[str] = None

    model_config = {"populate_by_name": True}


class CpuStatus(BaseModel):
    cpu_5sec: Optional[float] = Field(default=None, alias="cpu5Sec")
    cpu_1min: Optional[float] = Field(default=None, alias="cpu1Min")
    cpu_5min: Optional[float] = Field(default=None, alias="cpu5Min")
    raw: Optional[str] = None

    model_config = {"populate_by_name": True}


class MemoryStatus(BaseModel):
    processor_total: Optional[int] = Field(default=None, alias="processorTotal")
    processor_used: Optional[int] = Field(default=None, alias="processorUsed")
    processor_free: Optional[int] = Field(default=None, alias="processorFree")
    io_total: Optional[int] = Field(default=None, alias="ioTotal")
    io_used: Optional[int] = Field(default=None, alias="ioUsed")
    io_free: Optional[int] = Field(default=None, alias="ioFree")
    raw: Optional[str] = None

    model_config = {"populate_by_name": True}


class MacTableEntry(BaseModel):
    vlan: str
    mac: str
    type: str
    port: str


class ArpEntry(BaseModel):
    """One ``show ip arp`` row. Proves an IP/MAC association, nothing more."""

    ip: str
    mac: str
    age_minutes: Optional[int] = Field(default=None, alias="ageMinutes")
    interface: str = ""

    model_config = {"populate_by_name": True}


class MacTableResponse(BaseModel):
    entries: List[MacTableEntry]


class LogEntry(BaseModel):
    line: str
    severity: str = "info"  # info | notice | warning | critical


class LogsResponse(BaseModel):
    entries: List[LogEntry]
    raw: Optional[str] = None


class BackupResult(BaseModel):
    filename: str
    path: str
    size_bytes: int = Field(alias="sizeBytes")
    timestamp: datetime
    redacted_preview: str = Field(alias="redactedPreview")

    model_config = {"populate_by_name": True}


class AuditEvent(BaseModel):
    id: Optional[int] = None
    timestamp: datetime
    actor: str
    action: str
    commands: List[str]
    success: bool
    duration_ms: int = Field(alias="durationMs")
    output_path: Optional[str] = Field(default=None, alias="outputPath")
    error_type: Optional[str] = Field(default=None, alias="errorType")
    error_message: Optional[str] = Field(default=None, alias="errorMessage")
    before_state: Optional[str] = Field(default=None, alias="beforeState")
    after_state: Optional[str] = Field(default=None, alias="afterState")

    model_config = {"populate_by_name": True}


class AuditResponse(BaseModel):
    events: List[AuditEvent]


# --- Configuration history -------------------------------------------------

class ConfigurationHistoryEntry(BaseModel):
    id: int
    timestamp: datetime
    device_id: str = Field(alias="deviceId")
    fingerprint: str
    filename: str
    previous_id: Optional[int] = Field(default=None, alias="previousId")
    known_good: bool = Field(default=False, alias="knownGood")
    change_detected: bool = Field(default=False, alias="changeDetected")
    source: Literal["initial_observation", "external_or_unknown"]
    redacted_diff: List[str] = Field(default_factory=list, alias="redactedDiff")

    model_config = {"populate_by_name": True}


class ConfigurationHistoryResponse(BaseModel):
    entries: List[ConfigurationHistoryEntry]


# --- NetDevOps dry-run planning --------------------------------------------

class AccessPointPlanRequest(BaseModel):
    interface: str
    role: Literal["wireless-access-point"] = "wireless-access-point"
    enabled: bool = True
    vlan: int = Field(default=1, ge=1, le=4094)
    poe: Literal["auto", "never"] = "auto"
    portfast: bool = True

    @field_validator("interface")
    @classmethod
    def validate_plan_interface(cls, value: str) -> str:
        value = value.strip()
        if not value or len(value) > 64 or any(ord(char) < 32 for char in value):
            raise ValueError("Interface is invalid.")
        return value


class PlanCheck(BaseModel):
    name: str
    passed: bool
    detail: str


class DeploymentPlan(BaseModel):
    plan_id: str = Field(alias="planId")
    status: Literal["VALID", "INVALID"]
    target_interface: str = Field(alias="targetInterface")
    desired_state: Dict[str, Any] = Field(alias="desiredState")
    checks: List[PlanCheck]
    impact: str
    proposed_ios: List[str] = Field(alias="proposedIos")
    backup_required: bool = Field(default=True, alias="backupRequired")
    verification_commands: List[str] = Field(alias="verificationCommands")
    apply_available: Literal[False] = Field(default=False, alias="applyAvailable")

    model_config = {"populate_by_name": True}


class DashboardResponse(BaseModel):
    summary: SwitchSummary
    interfaces: InterfaceStatusResponse
    poe: PoeResponse
    errors: InterfaceErrorsResponse
    environment: EnvironmentStatus
    cpu: CpuStatus
    memory: MemoryStatus
    mac_table: MacTableResponse = Field(alias="macTable")
    logs: LogsResponse
    audit: AuditResponse
    telemetry: TelemetrySnapshotSummary
    events: NetworkEventsResponse
    topology: TopologyModel
    reconciliation: ReconciliationSummary
    configuration_history: ConfigurationHistoryResponse = Field(alias="configurationHistory")
    section_errors: Dict[str, str] = Field(default_factory=dict, alias="sectionErrors")

    model_config = {"populate_by_name": True}


# --- Beginner Lab Guide ----------------------------------------------------

class GuideOperation(BaseModel):
    id: str
    category: Literal["GETTING STARTED", "TROUBLESHOOTING", "NETWORKING", "SWITCH"]
    title: str
    question: str
    what_it_tells_you: str = Field(alias="whatItTellsYou")
    safety: Literal["READ ONLY"] = "READ ONLY"
    commands: List[str]
    requires_interface: bool = Field(default=False, alias="requiresInterface")

    model_config = {"populate_by_name": True}


class GuideCatalogResponse(BaseModel):
    operations: List[GuideOperation]


class GuideRunRequest(BaseModel):
    interface: Optional[str] = None

    @field_validator("interface")
    @classmethod
    def validate_interface_length(cls, value: Optional[str]) -> Optional[str]:
        if value is None:
            return None
        value = value.strip()
        if not value or len(value) > 64 or any(ord(char) < 32 for char in value):
            raise ValueError("Interface is invalid.")
        return value


class GuideRunResult(BaseModel):
    operation: GuideOperation
    observed_at: datetime = Field(alias="observedAt")
    result: Dict[str, Any]
    explanation: str
    warnings: List[str] = Field(default_factory=list)

    model_config = {"populate_by_name": True}


# --- Controlled operations --------------------------------------------------

OperationKind = Literal[
    "admin_up",
    "admin_down",
    "poe_auto",
    "poe_never",
    "set_description",
]


class OperationStage(BaseModel):
    """One step of a change transaction, streamed to the UI as it happens."""

    name: Literal["precheck", "backup", "execute", "verify", "audit", "rollback"]
    status: Literal["pending", "running", "ok", "failed", "skipped"]
    detail: str = ""


class OperationResult(BaseModel):
    operation_id: str = Field(alias="operationId")
    kind: OperationKind
    interface: str
    #: blocked  refused before anything was sent
    #: failed   sent, but rejected or unverified
    #: rolled_back  verification failed and the previous state was restored
    status: Literal["success", "failed", "blocked", "rolled_back"]
    detail: str = ""
    stages: List[OperationStage] = Field(default_factory=list)
    before_state: Optional[str] = Field(default=None, alias="beforeState")
    after_state: Optional[str] = Field(default=None, alias="afterState")
    commands: List[str] = Field(default_factory=list)
    duration_ms: int = Field(default=0, alias="durationMs")
    rolled_back: Optional[bool] = Field(default=None, alias="rolledBack")
    backup_path: Optional[str] = Field(default=None, alias="backupPath")
    #: True when the running configuration now differs from startup.
    requires_save: bool = Field(default=False, alias="requiresSave")
    at: datetime

    model_config = {"populate_by_name": True}


class OperationRequest(BaseModel):
    kind: OperationKind
    value: Optional[str] = Field(default=None, max_length=64)


class WriteLockStatus(BaseModel):
    """Two independent gates: installation capability, and session unlock."""

    capability: bool
    unlocked: bool
    unlocked_at: Optional[datetime] = Field(default=None, alias="unlockedAt")

    model_config = {"populate_by_name": True}


class ConfigSaveState(BaseModel):
    """Whether the running configuration has drifted from startup.

    A change is not permanent until somebody chooses to save it, and SwitchOps
    never saves on its own.
    """

    running_modified: bool = Field(default=False, alias="runningModified")
    last_change_at: Optional[datetime] = Field(default=None, alias="lastChangeAt")
    last_saved_at: Optional[datetime] = Field(default=None, alias="lastSavedAt")
    pending_operations: int = Field(default=0, alias="pendingOperations")
    detail: str = ""

    model_config = {"populate_by_name": True}


# --- Write actions ---------------------------------------------------------

class PortDescriptionRequest(BaseModel):
    description: str = Field(min_length=1, max_length=64)


class WriteActionResult(BaseModel):
    action: str
    interface: Optional[str] = None
    success: bool
    before: str
    after: str
    backup_path: str = Field(alias="backupPath")
    duration_ms: int = Field(alias="durationMs")

    model_config = {"populate_by_name": True}
