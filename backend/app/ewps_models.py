"""Typed contracts for the EWPS v0.1 shadow-mode research module."""
from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, Field, field_validator, model_validator


EWPS_MODEL_VERSION = "0.1.0"
EWPS_RELEASE_ID = "ewps-v0.1.0-alpha"
EWPS_MODE = "SHADOW"

TopologyEvidenceKey = Literal[
    "reciprocal_independent_direct",
    "one_sided_direct",
    "strong_inference",
    "weak_inference",
    "contradictory",
    "unknown",
]
CertaintyMode = Literal["product", "weighted_geometric"]
ExperimentStatus = Literal["CREATED", "RUNNING", "PAUSED", "COMPLETED"]
ExperimentKind = Literal["live", "simulator"]


def _camel(value: str) -> str:
    first, *rest = value.split("_")
    return first + "".join(part.capitalize() for part in rest)


class EWPSBaseModel(BaseModel):
    model_config = {
        "alias_generator": _camel,
        "populate_by_name": True,
        "extra": "forbid",
    }


class CertaintyWeights(EWPSBaseModel):
    freshness: float = Field(default=1.0, ge=0.0, le=10.0)
    stability: float = Field(default=1.0, ge=0.0, le=10.0)
    density: float = Field(default=1.0, ge=0.0, le=10.0)
    topology: float = Field(default=1.0, ge=0.0, le=10.0)

    @model_validator(mode="after")
    def require_one_dimension(self) -> "CertaintyWeights":
        if sum((self.freshness, self.stability, self.density, self.topology)) <= 0:
            raise ValueError("At least one certainty weight must be greater than zero.")
        return self


class HysteresisConfig(EWPSBaseModel):
    minimum_improvement: float = Field(default=0.08, ge=0.0, le=1.0)
    minimum_dwell_seconds: float = Field(default=30.0, ge=0.0, le=86_400.0)
    minimum_evidence_seconds: float = Field(default=15.0, ge=0.0, le=86_400.0)
    recovery_hold_down_seconds: float = Field(default=20.0, ge=0.0, le=86_400.0)


class EWPSConfig(EWPSBaseModel):
    lambda_decay: float = Field(default=0.035, alias="lambda", ge=0.0, le=10.0)
    density_k: float = Field(default=0.35, alias="k", gt=0.0, le=10.0)
    alpha: float = Field(default=1.0, ge=0.0, le=10.0)
    p_min: float = Field(default=0.25, ge=0.0, le=1.0)
    certainty_mode: CertaintyMode = "product"
    weights: CertaintyWeights = Field(default_factory=CertaintyWeights)
    hysteresis: HysteresisConfig = Field(default_factory=HysteresisConfig)
    latency_weight: float = Field(default=1.0, ge=0.0, le=100.0)
    jitter_weight: float = Field(default=0.5, ge=0.0, le=100.0)
    loss_weight: float = Field(default=10.0, ge=0.0, le=10_000.0)
    sample_interval_seconds: float = Field(default=5.0, ge=2.0, le=300.0)
    probe_count: int = Field(default=3, ge=1, le=5)
    rolling_window: int = Field(default=12, ge=2, le=1_000)

    @model_validator(mode="after")
    def require_performance_dimension(self) -> "EWPSConfig":
        if self.latency_weight + self.jitter_weight + self.loss_weight <= 0:
            raise ValueError("At least one raw performance cost weight must be greater than zero.")
        return self


class RawMetrics(EWPSBaseModel):
    latency_ms: float | None = Field(default=None, ge=0.0)
    jitter_ms: float | None = Field(default=None, ge=0.0)
    loss_pct: float | None = Field(default=None, ge=0.0, le=100.0)
    sample_count: int = Field(default=0, ge=0)
    reachable: bool = False
    interface_packets_sent: int | None = Field(default=None, ge=0)
    interface_packets_received: int | None = Field(default=None, ge=0)
    interface_errors: int | None = Field(default=None, ge=0)
    interface_drops: int | None = Field(default=None, ge=0)


class EvidenceInput(EWPSBaseModel):
    age_seconds: float | None = Field(default=None, ge=0.0)
    mean_ms: float | None = None
    stddev_ms: float | None = Field(default=None, ge=0.0)
    effective_samples: float = Field(default=0.0, ge=0.0)
    topology_evidence: TopologyEvidenceKey = "unknown"


class CertaintyComponents(EWPSBaseModel):
    freshness: float
    stability: float
    density: float
    topology: float
    composite: float
    index_description: str = "dimensionless evidence-confidence index"


class EWPSCalculation(EWPSBaseModel):
    model_version: str = EWPS_MODEL_VERSION
    path_id: str
    raw: RawMetrics
    evidence: EvidenceInput
    certainty: CertaintyComponents
    raw_cost: float | None = None
    ewps_cost: float | None = None
    eligible: bool = False
    valid: bool = False
    reasons: list[str] = Field(default_factory=list)


class AlgorithmChoice(EWPSBaseModel):
    algorithm: Literal[
        "lowest_latency",
        "lowest_loss",
        "performance_only",
        "ewps",
        "ewps_hysteresis",
    ]
    path_id: str | None = None
    cost: float | None = None
    reason: str


class HysteresisDecision(EWPSBaseModel):
    preferred_path_id: str | None = None
    challenger_path_id: str | None = None
    recommendation_changed: bool = False
    suppressed: bool = False
    would_switch: bool = False
    reason: str
    switch_blocked_by: str = "shadow_mode"


class DecisionPoint(EWPSBaseModel):
    timestamp: datetime
    decision_index: int = Field(ge=0)
    calculations: list[EWPSCalculation]
    algorithms: list[AlgorithmChoice]
    hysteresis: HysteresisDecision
    events: list[str] = Field(default_factory=list)
    explanation: str


class CandidatePath(EWPSBaseModel):
    path_id: str
    display_label: str
    adapter_name: str
    topology_evidence: TopologyEvidenceKey
    topology_detail: str
    reachable: bool | None = None
    eligible_for_live_measurement: bool = True


class ExperimentCreateRequest(EWPSBaseModel):
    name: str = Field(min_length=1, max_length=100)
    workload_label: str = Field(min_length=1, max_length=80)
    candidate_path_ids: list[str] = Field(min_length=1, max_length=8)
    config: EWPSConfig = Field(default_factory=EWPSConfig)

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


class ExperimentSession(EWPSBaseModel):
    experiment_id: str
    name: str
    workload_label: str
    status: ExperimentStatus
    kind: ExperimentKind = "live"
    mode: Literal["SHADOW"] = EWPS_MODE
    ewps_model_version: str = EWPS_MODEL_VERSION
    release_id: str = EWPS_RELEASE_ID
    config: EWPSConfig
    candidate_path_ids: list[str]
    created_at: datetime
    started_at: datetime | None = None
    ended_at: datetime | None = None
    paused_at: datetime | None = None
    total_measurements: int = 0
    decision_points: int = 0


class ExperimentTimeline(EWPSBaseModel):
    session: ExperimentSession
    decisions: list[DecisionPoint]


class ExperimentSummary(EWPSBaseModel):
    experiment_id: str
    duration_seconds: float
    total_samples: int
    decision_points: int
    measurements_per_path: dict[str, int]
    average_confidence_per_path: dict[str, float | None]
    minimum_confidence_per_path: dict[str, float | None]
    preferred_percent_per_path: dict[str, float]
    algorithm_disagreement_rate: float
    ewps_recommendation_changes: int
    hysteresis_suppressed_changes: int
    ineligible_samples_per_path: dict[str, int]
    ineligible_seconds_per_path: dict[str, float]
    stale_evidence_events: int
    instability_events: int
    telemetry_failures: int
    notable_decision_events: list[str]


class ReplayRequest(EWPSBaseModel):
    config: EWPSConfig | None = None


class ReplayResult(EWPSBaseModel):
    source_experiment_id: str
    model_version: str = EWPS_MODEL_VERSION
    config: EWPSConfig
    deterministic_digest: str
    decisions: list[DecisionPoint]


class SimulatorScenario(EWPSBaseModel):
    scenario_id: str
    name: str
    description: str


class SimulatorRunRequest(EWPSBaseModel):
    scenario_id: str
    config: EWPSConfig = Field(default_factory=EWPSConfig)


class SimulatorRunResult(EWPSBaseModel):
    scenario: SimulatorScenario
    config: EWPSConfig
    decisions: list[DecisionPoint]
    summary: dict[str, Any]
