"""Typed contracts for EWPS v0.2 controlled dual-path research.

The v0.1 contracts remain in :mod:`ewps_models`.  Keeping these models
separate is intentional: a stored ``model_version`` selects the parser and
engine, so opening a historical session cannot silently apply v0.2 defaults.
"""
from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

from pydantic import Field, field_validator, model_validator

from .ewps_models import (
    AlgorithmChoice,
    EWPSBaseModel,
    HysteresisConfig,
    HysteresisDecision,
    TopologyEvidenceKey,
)


EWPS_V2_MODEL_VERSION = "0.2.0"
EWPS_V2_RELEASE_ID = "ewps-v0.2.4-alpha"

#: Releases whose experiments were recorded with authoritative scenario-phase
#: snapshots, and therefore export as schema v4.
#:
#: This is deliberately a set rather than an equality against
#: ``EWPS_V2_RELEASE_ID``. Sessions carry the release that wrote them and no
#: separate schema field, so "was this recorded by the newest build?" was being
#: used to mean "is this schema v4?". Those coincided only while every release
#: also introduced a new schema: the first release that did not would have
#: silently re-exported existing v4 records through the older path and dropped
#: their phase events and summaries.
SCHEMA_V4_RELEASE_IDS = frozenset(
    {
        "ewps-v0.2.3-alpha",
        "ewps-v0.2.4-alpha",
    }
)

CandidateLifecycle = Literal[
    "VIABLE",
    "PROBING",
    "PERSISTENTLY_UNAVAILABLE",
    "RECOVERING",
    "DISABLED",
]
CandidateSourceKind = Literal["real_interface", "controlled_lab"]
ExperimentSourceMode = Literal[
    "REAL_INTERFACES",
    "CONTROLLED_DUAL_PATH",
    "SIMULATOR",
    "LEGACY_UNBOUND",
]
InitialVerificationStatus = Literal["VERIFIED", "NOT_APPLICABLE", "LEGACY_UNKNOWN"]
TelemetryState = Literal[
    "validated",
    "transient_failure",
    "candidate_unavailable",
    "reprobe_deferred",
    "evidence_stale",
    "controlled_lab_lost",
]
EligibilityState = Literal[
    "ELIGIBLE",
    "ELIGIBLE_TOPOLOGY_WEAK",
    "ELIGIBLE_TOPOLOGY_UNKNOWN",
    "PERFORMANCE_EVIDENCE_INSUFFICIENT",
    "TOPOLOGY_CONFLICT",
    "TELEMETRY_UNAVAILABLE",
    "UNREACHABLE",
]


class PerformanceConfidenceWeights(EWPSBaseModel):
    freshness: float = Field(default=0.40, ge=0.0, le=1.0)
    stability: float = Field(default=0.35, ge=0.0, le=1.0)
    density: float = Field(default=0.25, ge=0.0, le=1.0)

    @model_validator(mode="after")
    def normalized(self) -> "PerformanceConfidenceWeights":
        total = self.freshness + self.stability + self.density
        if abs(total - 1.0) > 1e-9:
            raise ValueError("Performance-confidence weights must sum to 1.0.")
        return self


class EWPSV2Config(EWPSBaseModel):
    lambda_decay: float = Field(default=0.035, alias="lambda", ge=0.0, le=10.0)
    density_k: float = Field(default=0.08, alias="k", gt=0.0, le=10.0)
    alpha: float = Field(default=1.0, ge=0.0, le=10.0)
    beta: float = Field(default=0.25, ge=0.0, le=10.0)
    p_perf_min: float = Field(default=0.50, ge=0.0, le=1.0)
    weights: PerformanceConfidenceWeights = Field(default_factory=PerformanceConfidenceWeights)
    hysteresis: HysteresisConfig = Field(default_factory=HysteresisConfig)
    latency_weight: float = Field(default=1.0, ge=0.0, le=100.0)
    jitter_weight: float = Field(default=0.5, ge=0.0, le=100.0)
    loss_weight: float = Field(default=10.0, ge=0.0, le=10_000.0)
    sample_interval_seconds: float = Field(default=5.0, ge=2.0, le=300.0)
    probe_count: int = Field(default=5, ge=1, le=5)
    rolling_window: int = Field(default=12, ge=2, le=1_000)
    loss_window_probes: int = Field(default=50, ge=10, le=300)
    unavailable_failure_threshold: int = Field(default=3, ge=2, le=20)
    unavailable_reprobe_cycles: int = Field(default=6, ge=2, le=120)

    @model_validator(mode="after")
    def require_performance_dimension(self) -> "EWPSV2Config":
        if self.latency_weight + self.jitter_weight + self.loss_weight <= 0:
            raise ValueError("At least one raw performance cost weight must be greater than zero.")
        return self


class V2RawMetrics(EWPSBaseModel):
    latency_ms: float | None = Field(default=None, ge=0.0)
    rolling_latency_ms: float | None = Field(default=None, ge=0.0)
    jitter_ms: float | None = Field(default=None, ge=0.0)
    rolling_jitter_ms: float | None = Field(default=None, ge=0.0)
    loss_pct: float | None = Field(default=None, ge=0.0, le=100.0)
    rolling_loss_pct: float | None = Field(default=None, ge=0.0, le=100.0)
    sample_count: int = Field(default=0, ge=0)
    loss_sample_count: int = Field(default=0, ge=0)
    probe_outcomes: list[bool] = Field(default_factory=list, max_length=5)
    reachable: bool | None = None
    routing_metrics_usable: bool = False
    telemetry_state: TelemetryState = "candidate_unavailable"
    candidate_lifecycle: CandidateLifecycle = "PROBING"
    transient_failure: bool = False
    candidate_unavailable_event: bool = False
    recovery_event: bool = False
    interface_packets_sent: int | None = Field(default=None, ge=0)
    interface_packets_received: int | None = Field(default=None, ge=0)
    interface_errors: int | None = Field(default=None, ge=0)
    interface_drops: int | None = Field(default=None, ge=0)


class V2EvidenceInput(EWPSBaseModel):
    age_seconds: float | None = Field(default=None, ge=0.0)
    mean_ms: float | None = Field(default=None, gt=0.0)
    stddev_ms: float | None = Field(default=None, ge=0.0)
    effective_samples: float = Field(default=0.0, ge=0.0)
    topology_evidence: TopologyEvidenceKey = "unknown"
    collection_started_at: datetime | None = None
    observation_validated_at: datetime | None = None
    collection_duration_ms: float | None = Field(default=None, ge=0.0)


class V2ConfidenceComponents(EWPSBaseModel):
    freshness: float = Field(ge=0.0, le=1.0)
    stability: float = Field(ge=0.0, le=1.0)
    density: float = Field(ge=0.0, le=1.0)
    performance: float = Field(ge=0.0, le=1.0)
    topology: float = Field(ge=0.0, le=1.0)
    topology_penalty: float = Field(ge=1.0)
    index_description: str = "dimensionless performance evidence-confidence index"


class V2EWPSCalculation(EWPSBaseModel):
    model_version: Literal["0.2.0"] = EWPS_V2_MODEL_VERSION
    path_id: str
    raw: V2RawMetrics
    evidence: V2EvidenceInput
    confidence: V2ConfidenceComponents
    raw_cost: float | None = None
    ewps_cost: float | None = None
    eligible: bool = False
    eligibility_state: EligibilityState
    valid: bool = False
    reasons: list[str] = Field(default_factory=list)


class V2CadenceObservation(EWPSBaseModel):
    """Scheduler instrumentation attached to a live measurement cycle.

    This is deliberately separate from the EWPS evidence inputs: cadence
    describes when the collector ran and never participates in model math.
    Historical v0.2 decision rows omit it and continue to validate unchanged.
    """

    configured_interval_seconds: float = Field(gt=0.0)
    cycle_started_at: datetime
    cycle_completed_at: datetime
    collection_duration_ms: float = Field(ge=0.0)
    actual_start_to_start_seconds: float | None = Field(default=None, ge=0.0)
    cadence_overrun_count: int = Field(default=0, ge=0)


PhaseVerification = Literal["PASSED", "FAILED", "NOT_APPLICABLE"]
ExperimentEventType = Literal["SCENARIO_PHASE_CHANGED", "SCENARIO_PHASE_APPLY_FAILED"]


class V2NormalizedNetemConfig(EWPSBaseModel):
    """Privacy-safe, deterministic netem parameters; never shell text."""

    kind: Literal["netem"] = "netem"
    delay_ms: float | None = Field(default=None, ge=0.0)
    jitter_ms: float | None = Field(default=None, ge=0.0)
    delay_correlation_pct: float | None = Field(default=None, ge=0.0, le=100.0)
    distribution: str | None = None
    loss_pct: float | None = Field(default=None, ge=0.0, le=100.0)
    loss_correlation_pct: float | None = Field(default=None, ge=0.0, le=100.0)


class V2PhasePathProfile(EWPSBaseModel):
    requested_profile_id: "LabProfileName"
    applied_profile_id: "LabProfileName | None" = None
    requested_configuration: V2NormalizedNetemConfig
    applied_configuration: V2NormalizedNetemConfig | None = None
    verification: PhaseVerification
    verification_detail: str


class V2ScenarioPhaseSnapshot(EWPSBaseModel):
    scenario_id: "LabScenarioName"
    phase_index: int = Field(ge=0)
    phase_id: str = Field(min_length=1, max_length=80)
    lab_instance_id: str
    path_profiles: dict[str, V2PhasePathProfile]


class V2ExperimentEvent(EWPSBaseModel):
    event_id: str
    event_type: ExperimentEventType
    timestamp: datetime
    completed_at: datetime
    experiment_id: str
    scenario_id: "LabScenarioName"
    previous_phase_index: int = Field(ge=0)
    previous_phase_id: str
    new_phase_index: int = Field(ge=0)
    new_phase_id: str
    application_succeeded: bool
    lab_instance_id: str
    affected_path_ids: list[str]
    path_profiles: dict[str, V2PhasePathProfile]
    verification: PhaseVerification
    detail: str


class LabPhaseTransitionResult(EWPSBaseModel):
    requested_at: datetime
    completed_at: datetime
    scenario_id: "LabScenarioName"
    previous_phase_index: int
    previous_phase_id: str
    new_phase_index: int
    new_phase_id: str
    application_succeeded: bool
    lab_instance_id: str
    affected_path_ids: list[str]
    path_profiles: dict[str, V2PhasePathProfile]
    verification: PhaseVerification
    detail: str


class V2DecisionPoint(EWPSBaseModel):
    timestamp: datetime
    decision_index: int = Field(ge=0)
    calculations: list[V2EWPSCalculation]
    algorithms: list[AlgorithmChoice]
    hysteresis: HysteresisDecision
    events: list[str] = Field(default_factory=list)
    explanation: str
    cadence: V2CadenceObservation | None = None
    scenario_phase: V2ScenarioPhaseSnapshot | None = None


class V2CandidatePath(EWPSBaseModel):
    path_id: str
    display_label: str
    adapter_name: str
    source_kind: CandidateSourceKind
    lifecycle: CandidateLifecycle = "PROBING"
    topology_evidence: TopologyEvidenceKey
    topology_detail: str
    diversity_claim: str = "No physical, ISP, or independent failure-domain diversity is claimed."
    reachable: bool | None = None
    eligible_for_live_measurement: bool = True


class V2CandidateSnapshot(EWPSBaseModel):
    """Immutable, privacy-safe identity and provenance captured at creation."""

    path_id: str
    display_label: str
    adapter_name: str
    source_kind: CandidateSourceKind
    topology_evidence: TopologyEvidenceKey
    topology_detail: str
    diversity_claim: str = "No physical, ISP, or independent failure-domain diversity is claimed."


class V2ExperimentCreateRequest(EWPSBaseModel):
    name: str = Field(min_length=1, max_length=100)
    workload_label: str = Field(min_length=1, max_length=80)
    source_mode: Literal["REAL_INTERFACES", "CONTROLLED_DUAL_PATH"]
    candidate_path_ids: list[str] = Field(min_length=1, max_length=8)
    controlled_scenario: "LabScenarioName | None" = None
    config: EWPSV2Config = Field(default_factory=EWPSV2Config)

    @field_validator("name", "workload_label")
    @classmethod
    def safe_operator_label(cls, value: str) -> str:
        clean = value.strip()
        if not clean or any(ord(char) < 32 or ord(char) == 127 for char in clean):
            raise ValueError("Label contains invalid characters.")
        return clean

    @field_validator("candidate_path_ids")
    @classmethod
    def unique_paths(cls, value: list[str]) -> list[str]:
        if len(set(value)) != len(value):
            raise ValueError("Candidate path IDs must be unique.")
        return value

    @model_validator(mode="after")
    def source_and_scenario_agree(self) -> "V2ExperimentCreateRequest":
        if self.source_mode == "REAL_INTERFACES" and self.controlled_scenario is not None:
            raise ValueError("A real-interface experiment cannot reference a controlled-lab scenario.")
        return self


class V2ExperimentSession(EWPSBaseModel):
    experiment_id: str
    name: str
    workload_label: str
    status: Literal["CREATED", "RUNNING", "PAUSED", "COMPLETED"]
    kind: Literal["live", "simulator"] = "live"
    mode: Literal["SHADOW"] = "SHADOW"
    ewps_model_version: Literal["0.2.0"] = EWPS_V2_MODEL_VERSION
    release_id: Literal[
        "ewps-v0.2.0-alpha",
        "ewps-v0.2.1-alpha",
        "ewps-v0.2.2-alpha",
        "ewps-v0.2.3-alpha",
        "ewps-v0.2.4-alpha",
    ] = EWPS_V2_RELEASE_ID
    config: EWPSV2Config
    source_mode: ExperimentSourceMode = "LEGACY_UNBOUND"
    candidate_path_ids: list[str]
    candidate_snapshot: list[V2CandidateSnapshot] = Field(default_factory=list)
    lab_instance_id: str | None = None
    lab_topology_version: str | None = None
    initial_verification_status: InitialVerificationStatus = "LEGACY_UNKNOWN"
    controlled_impairment_scenario: "LabScenarioName | None" = None
    initial_scenario_phase: V2ScenarioPhaseSnapshot | None = None
    created_at: datetime
    started_at: datetime | None = None
    ended_at: datetime | None = None
    paused_at: datetime | None = None
    total_measurements: int = 0
    decision_points: int = 0


class V2ExperimentTimeline(EWPSBaseModel):
    session: V2ExperimentSession
    decisions: list[V2DecisionPoint]
    events: list[V2ExperimentEvent] = Field(default_factory=list)


class DistributionSummary(EWPSBaseModel):
    minimum: float | None = None
    mean: float | None = None
    median: float | None = None
    maximum: float | None = None


class V2ExperimentSummary(EWPSBaseModel):
    experiment_id: str
    duration_seconds: float
    total_samples: int
    decision_points: int
    configured_interval_seconds: float
    observed_start_to_start_seconds: DistributionSummary
    observed_collection_duration_ms: DistributionSummary
    cadence_overrun_count: int
    measurements_per_path: dict[str, int]
    usable_path_count_over_time: list[dict[str, Any]]
    unavailable_candidate_count: int
    candidate_unavailable_events: int
    transient_failures_on_viable_paths: int
    recovery_events: int
    performance_confidence_per_path: dict[str, dict[str, float | None]]
    topology_confidence_per_path: dict[str, dict[str, float | None]]
    ewps_cost_distribution_per_path: dict[str, DistributionSummary]
    raw_cost_distribution_per_path: dict[str, DistributionSummary]
    algorithm_disagreement_percentage: float
    pairwise_disagreement_matrix: dict[str, dict[str, float]]
    preference_duration_seconds_per_algorithm_path: dict[str, dict[str, float]]
    recommendation_switches_per_algorithm: dict[str, int]
    hysteresis_suppressed_switches: int
    below_evidence_threshold_seconds_per_path: dict[str, float]
    rolling_loss_events: int
    stale_evidence_events: int
    ewps_vs_lowest_latency_difference_percentage: float
    disagreement_evidence_components: dict[str, int]
    most_common_disagreement_component: str | None = None
    notable_decision_events: list[str]
    phase_summaries: list["V2PhaseSummary"] = Field(default_factory=list)


class V2PhaseSummary(EWPSBaseModel):
    scenario_id: "LabScenarioName"
    phase_index: int = Field(ge=0)
    phase_id: str
    started_at: datetime
    ended_at: datetime
    duration_seconds: float = Field(ge=0.0)
    decision_points: int = Field(ge=0)
    measurements_per_path: dict[str, int]
    performance_confidence_per_path: dict[str, DistributionSummary]
    raw_cost_distribution_per_path: dict[str, DistributionSummary]
    ewps_cost_distribution_per_path: dict[str, DistributionSummary]
    algorithm_preference_counts: dict[str, dict[str, int]]
    algorithm_disagreement_count: int = Field(ge=0)
    hysteresis_suppressions: int = Field(ge=0)
    path_eligibility_seconds: dict[str, float]
    telemetry_failures: int = Field(ge=0)
    stale_events: int = Field(ge=0)


class V2ReplayResult(EWPSBaseModel):
    source_experiment_id: str
    model_version: Literal["0.2.0"] = EWPS_V2_MODEL_VERSION
    source_mode: ExperimentSourceMode
    candidate_snapshot: list[V2CandidateSnapshot]
    lab_instance_id: str | None = None
    lab_topology_version: str | None = None
    controlled_impairment_scenario: "LabScenarioName | None" = None
    config: EWPSV2Config
    deterministic_digest: str
    decisions: list[V2DecisionPoint]
    events: list[V2ExperimentEvent] = Field(default_factory=list)


class V2ReplayRequest(EWPSBaseModel):
    config: EWPSV2Config | None = None


class VersionedReplayRequest(EWPSBaseModel):
    """Unambiguous transport model; the stored version selects validation."""

    config: dict[str, Any] | None = None


class V2SimulatorScenario(EWPSBaseModel):
    scenario_id: str
    name: str
    description: str
    expected_research_pattern: str


class V2SimulatorRunRequest(EWPSBaseModel):
    scenario_id: str
    config: EWPSV2Config = Field(default_factory=EWPSV2Config)


class V2SimulatorRunResult(EWPSBaseModel):
    source_mode: Literal["SIMULATOR"] = "SIMULATOR"
    scenario: V2SimulatorScenario
    config: EWPSV2Config
    decisions: list[V2DecisionPoint]
    summary: dict[str, Any]
    v1_comparison: dict[str, Any] | None = None


class ExportSaveRequest(EWPSBaseModel):
    format: Literal["jsonl", "json", "csv"] = "jsonl"


class ExportSaveResult(EWPSBaseModel):
    saved_path: str
    filename: str
    format: Literal["jsonl", "json", "csv"]
    folder_open_available: bool


LabProfileName = Literal[
    "fast-stable",
    "slow-stable",
    "fast-noisy",
    "moderate-jitter",
    "intermittent-loss",
    "sustained-loss",
    "telemetry-stale",
    "temporary-failure",
    "recovery",
    "crossing-latency",
]
LabScenarioName = Literal[
    "conventional-agreement",
    "faster-epistemically-weak",
    "raw-metric-flapping",
    "evidence-outage",
    "recovery",
]


class LabProfileRequest(EWPSBaseModel):
    path_id: Literal["lab-path-a", "lab-path-b"]
    profile: LabProfileName


class LabScenarioRequest(EWPSBaseModel):
    scenario_id: LabScenarioName


class LabPathStatus(EWPSBaseModel):
    path_id: Literal["lab-path-a", "lab-path-b"]
    display_label: str
    profile: LabProfileName
    independently_validated: bool = False
    last_latency_ms: float | None = None
    last_validated_at: datetime | None = None


class LabStatus(EWPSBaseModel):
    available: bool
    ready: bool
    state: Literal["LAB_NOT_CREATED", "LAB_UNVERIFIED", "LAB_READY", "LAB_LOST"]
    prerequisites_passed: bool = False
    lab_instance_id: str | None = None
    topology_version: str
    explicit_start_required: bool = True
    architecture: str = "contained WSL2 network namespaces with two separate gateway/veth chains"
    diversity_claim: str = "Controlled logical test paths; no physical, ISP, or independent failure-domain diversity is claimed."
    message: str
    scenario_id: LabScenarioName | None = None
    scenario_phase: int = 0
    scenario_phase_id: str | None = None
    scenario_phase_count: int = 0
    paths: list[LabPathStatus] = Field(default_factory=list)


class LabScenarioAdvanceResponse(EWPSBaseModel):
    status: LabStatus
    event: V2ExperimentEvent | None = None
