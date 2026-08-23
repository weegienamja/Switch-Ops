"""Typed contracts for durable, single-step Change Assurance sessions.

The operation executor remains the authority for how a bounded IOS change is
performed.  These models describe why it is being performed, what current
evidence says it may affect, and what was observed before and after.
"""
from __future__ import annotations

from datetime import datetime
from typing import Any, Literal, Optional

from pydantic import BaseModel, Field, field_validator, model_validator

from .models import OperationKind, OperationResult, OperationStage


ChangeSessionStatus = Literal[
    "planned",
    "preflight",
    "blocked",
    "ready",
    "executing",
    "verifying",
    "rolling_back",
    "rolled_back",
    "succeeded",
    "succeeded_with_warnings",
    "indeterminate",
]
PreflightCheckStatus = Literal["pass", "warn", "info", "block"]


class ChangeStep(BaseModel):
    interface: str
    kind: OperationKind
    value: Optional[str] = Field(default=None, max_length=64)

    @field_validator("interface")
    @classmethod
    def validate_interface(cls, value: str) -> str:
        value = value.strip()
        if not value or len(value) > 64 or any(ord(char) < 32 for char in value):
            raise ValueError("Interface is invalid.")
        return value


class ChangePlanRequest(BaseModel):
    # The container is intentionally future-shaped, while v0.6 refuses to
    # imply atomicity across more than one bounded operation.
    steps: list[ChangeStep] = Field(min_length=1, max_length=1)
    summary: Optional[str] = Field(default=None, max_length=240)


class ExpectedChangeEffect(BaseModel):
    category: Literal["configuration", "interface", "topology", "health"]
    field: str
    expectation: str
    required: bool = False


class DeclaredChangeIntent(BaseModel):
    summary: str
    expected_postconditions: list[ExpectedChangeEffect] = Field(
        default_factory=list, alias="expectedPostconditions"
    )
    unacceptable_effects: list[str] = Field(
        default_factory=list, alias="unacceptableEffects"
    )

    model_config = {"populate_by_name": True}


class ChangePlan(BaseModel):
    id: str
    device_id: str = Field(alias="deviceId")
    target_interface: str = Field(alias="targetInterface")
    steps: list[ChangeStep]
    declared_intent: DeclaredChangeIntent = Field(alias="declaredIntent")
    created_at: datetime = Field(alias="createdAt")

    model_config = {"populate_by_name": True}

    @model_validator(mode="after")
    def enforce_v06_scope(self) -> "ChangePlan":
        if len(self.steps) != 1:
            raise ValueError("SwitchOps v0.6 supports exactly one bounded operation per plan.")
        if self.steps[0].interface != self.target_interface:
            raise ValueError("The plan target and operation interface must match.")
        return self


class PreflightCheck(BaseModel):
    code: str
    label: str
    status: PreflightCheckStatus
    detail: str
    evidence: list[str] = Field(default_factory=list)


class BlastRadius(BaseModel):
    target_interface: str = Field(alias="targetInterface")
    attached_endpoints: int = Field(default=0, alias="attachedEndpoints")
    learned_behind: int = Field(default=0, alias="learnedBehind")
    expected_relationship: Optional[str] = Field(default=None, alias="expectedRelationship")
    control_path: Literal["clear", "possible", "confirmed", "unknown"] = Field(
        default="unknown", alias="controlPath"
    )
    control_path_detail: str = Field(default="", alias="controlPathDetail")
    confidence_limitations: list[str] = Field(
        default_factory=list, alias="confidenceLimitations"
    )

    model_config = {"populate_by_name": True}


class AssuranceInterfaceSnapshot(BaseModel):
    port: str
    present: bool = True
    admin_state: str = Field(default="unknown", alias="adminState")
    oper_state: str = Field(default="unknown", alias="operState")
    description: str = ""
    vlan: str = ""
    speed: str = ""
    duplex: str = ""
    poe_admin: str = Field(default="unknown", alias="poeAdmin")
    poe_oper: str = Field(default="unknown", alias="poeOper")
    error_total: int = Field(default=0, alias="errorTotal")
    learned_mac_count: int = Field(default=0, alias="learnedMacCount")

    model_config = {"populate_by_name": True}


class AssuranceConfigurationSnapshot(BaseModel):
    running_fingerprint: str = Field(alias="runningFingerprint")
    startup_fingerprint: str = Field(alias="startupFingerprint")
    running_differs_from_startup: bool = Field(alias="runningDiffersFromStartup")
    rollback_representable: bool = Field(alias="rollbackRepresentable")

    model_config = {"populate_by_name": True}


class AssuranceTopologySnapshot(BaseModel):
    relationships: list[str] = Field(default_factory=list)
    attached_entity_ids: list[str] = Field(default_factory=list, alias="attachedEntityIds")
    learned_behind_entity_ids: list[str] = Field(
        default_factory=list, alias="learnedBehindEntityIds"
    )
    expected_relationship: Optional[str] = Field(default=None, alias="expectedRelationship")
    reconciliation_state: Optional[str] = Field(default=None, alias="reconciliationState")
    target_role: str = Field(default="unknown", alias="targetRole")
    local_host_correlated: bool = Field(default=False, alias="localHostCorrelated")
    other_topology_fingerprint: str = Field(alias="otherTopologyFingerprint")

    model_config = {"populate_by_name": True}


class AssuranceHealthSnapshot(BaseModel):
    connection_state: str = Field(alias="connectionState")
    device_health: str = Field(default="UNKNOWN", alias="deviceHealth")
    target_health: str = Field(default="UNKNOWN", alias="targetHealth")
    target_error_total: int = Field(default=0, alias="targetErrorTotal")

    model_config = {"populate_by_name": True}


class AssuranceEvidenceSnapshot(BaseModel):
    topology_observed_at: Optional[datetime] = Field(default=None, alias="topologyObservedAt")
    freshness: str = "unknown"
    evidence_ids: list[str] = Field(default_factory=list, alias="evidenceIds")

    model_config = {"populate_by_name": True}


class AssuranceSnapshot(BaseModel):
    captured_at: datetime = Field(alias="capturedAt")
    device_id: str = Field(alias="deviceId")
    target_interface: str = Field(alias="targetInterface")
    configuration: AssuranceConfigurationSnapshot
    target: AssuranceInterfaceSnapshot
    other_interfaces: list[AssuranceInterfaceSnapshot] = Field(
        default_factory=list, alias="otherInterfaces"
    )
    topology: AssuranceTopologySnapshot
    health: AssuranceHealthSnapshot
    evidence: AssuranceEvidenceSnapshot

    model_config = {"populate_by_name": True}


class ChangePreflight(BaseModel):
    evaluated_at: datetime = Field(alias="evaluatedAt")
    outcome: Literal["ready", "blocked"]
    checks: list[PreflightCheck] = Field(default_factory=list)
    impact: BlastRadius
    snapshot: Optional[AssuranceSnapshot] = None

    model_config = {"populate_by_name": True}


class ChangeDifference(BaseModel):
    scope: Literal["target", "unrelated", "configuration", "topology", "health"]
    field: str
    before: Any = None
    after: Any = None
    assessment: Literal["expected", "warning", "info"]
    detail: str
    interface: Optional[str] = None


class ChangeComparison(BaseModel):
    evaluated_at: datetime = Field(alias="evaluatedAt")
    direct_postcondition: Literal["met", "not_met", "unknown"] = Field(
        alias="directPostcondition"
    )
    differences: list[ChangeDifference] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)
    summary: str

    model_config = {"populate_by_name": True}


class ChangeSession(BaseModel):
    id: str
    plan: ChangePlan
    status: ChangeSessionStatus
    preflight: Optional[ChangePreflight] = None
    before_snapshot: Optional[AssuranceSnapshot] = Field(default=None, alias="beforeSnapshot")
    after_snapshot: Optional[AssuranceSnapshot] = Field(default=None, alias="afterSnapshot")
    comparison: Optional[ChangeComparison] = None
    operation_result: Optional[OperationResult] = Field(default=None, alias="operationResult")
    operation_stages: list[OperationStage] = Field(default_factory=list, alias="operationStages")
    outcome_detail: str = Field(default="", alias="outcomeDetail")
    created_at: datetime = Field(alias="createdAt")
    updated_at: datetime = Field(alias="updatedAt")

    model_config = {"populate_by_name": True}


class ChangeSessionList(BaseModel):
    sessions: list[ChangeSession] = Field(default_factory=list)
