"""Provider-neutral contracts for the SwitchOps Unified Lab.

These models deliberately describe compact claims rather than provider payloads.
Collectors must normalize into this vocabulary and discard their raw responses.
Protected identifier values are local HMAC tokens, never raw serials, MACs, or
management addresses.
"""
from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field


ProviderKind = Literal[
    "catalyst-ios",
    "meraki-dashboard",
    "switchops-intent",
    "switchops-history",
]
EvidenceStrength = Literal["strong", "supporting", "weak"]
EvidenceFreshness = Literal["current", "aging", "stale", "historical"]
ClaimField = Literal[
    "existence",
    "identity",
    "attachment",
    "relationship",
    "availability",
    "name",
    "vendor",
    "model",
    "category",
    "vlan",
    "port",
    "uplink",
]
IdentifierKind = Literal[
    "serial",
    "chassis-mac",
    "device-mac",
    "management-address",
    "provider-id",
    "direct-adjacency",
    "reciprocal-adjacency",
    "name",
    "model",
]
IdentityLinkState = Literal[
    "confirmed",
    "candidate",
    "rejected",
    "conflicted",
    "stale",
]
OperatorIdentityDecision = Literal["confirm", "reject", "clear"]
CrossProviderState = Literal[
    "AGREED",
    "PROVIDER_ONLY",
    "STALE",
    "AMBIGUOUS",
    "CONFLICT",
    "UNKNOWN",
]
SourceHealthState = Literal[
    "not-configured",
    "healthy",
    "partial",
    "rate-limited",
    "unavailable",
    "stale",
]


class ProviderScope(BaseModel):
    """Non-secret scope identifiers for one provider collection."""

    organization_id: str | None = Field(default=None, alias="organizationId")
    network_id: str | None = Field(default=None, alias="networkId")
    device_ref: str | None = Field(default=None, alias="deviceRef")

    model_config = {"populate_by_name": True}


class EvidenceProvenance(BaseModel):
    """Structured provenance attached to every normalized claim."""

    provider: ProviderKind
    source_kind: str = Field(alias="sourceKind", min_length=1, max_length=64)
    source_object_ref: str = Field(alias="sourceObjectRef", min_length=1, max_length=160)
    scope: ProviderScope = Field(default_factory=ProviderScope)
    observed_at: datetime = Field(alias="observedAt")
    collected_at: datetime = Field(alias="collectedAt")
    complete: bool = True

    model_config = {"populate_by_name": True}


class ProviderIdentifier(BaseModel):
    """One protected identifier supplied by a provider.

    ``protected_value`` is a local HMAC token. ``globally_administered`` is
    meaningful only for MAC identifiers; locally administered/randomized MACs
    are never admitted as durable identifiers by the normalizers.
    """

    kind: IdentifierKind
    protected_value: str = Field(alias="protectedValue", min_length=12, max_length=96)
    strength: EvidenceStrength
    globally_administered: bool | None = Field(default=None, alias="globallyAdministered")
    provenance_ref: str = Field(alias="provenanceRef", min_length=1, max_length=160)

    model_config = {"populate_by_name": True}


class NormalizedClaim(BaseModel):
    """Compact provider-neutral claim; provider payloads never enter this model."""

    id: str
    provider: ProviderKind
    subject_ref: str = Field(alias="subjectRef")
    field: ClaimField
    value: str | bool | int | float | None = None
    object_ref: str | None = Field(default=None, alias="objectRef")
    strength: EvidenceStrength
    freshness: EvidenceFreshness = "current"
    provenance: EvidenceProvenance
    detail: str = ""

    model_config = {"populate_by_name": True}


class ProviderEntity(BaseModel):
    """One entity as reported by exactly one provider."""

    id: str
    provider: ProviderKind
    provider_ref: str = Field(alias="providerRef")
    label: str
    category: str = "unknown"
    vendor: str | None = None
    model: str | None = None
    identifiers: list[ProviderIdentifier] = Field(default_factory=list)
    claim_ids: list[str] = Field(default_factory=list, alias="claimIds")
    observed_at: datetime = Field(alias="observedAt")
    freshness: EvidenceFreshness = "current"

    model_config = {"populate_by_name": True}


class IdentityReason(BaseModel):
    kind: Literal["agreement", "conflict", "support", "hint", "operator"]
    field: IdentifierKind | Literal["operator-decision"]
    strength: EvidenceStrength
    summary: str
    provenance_refs: list[str] = Field(default_factory=list, alias="provenanceRefs")

    model_config = {"populate_by_name": True}


class IdentityLink(BaseModel):
    """A retained identity decision between two provider-local entities."""

    id: str
    left_entity_id: str = Field(alias="leftEntityId")
    right_entity_id: str = Field(alias="rightEntityId")
    state: IdentityLinkState
    automatic: bool = True
    reasons: list[IdentityReason] = Field(default_factory=list)
    evaluated_at: datetime = Field(alias="evaluatedAt")
    decided_at: datetime | None = Field(default=None, alias="decidedAt")

    model_config = {"populate_by_name": True}


class IdentityConflict(BaseModel):
    id: str
    left_entity_id: str = Field(alias="leftEntityId")
    right_entity_id: str = Field(alias="rightEntityId")
    field: IdentifierKind
    summary: str
    provenance_refs: list[str] = Field(default_factory=list, alias="provenanceRefs")

    model_config = {"populate_by_name": True}


class OperatorIdentityDecisionRequest(BaseModel):
    link_id: str = Field(alias="linkId", min_length=1, max_length=128)
    decision: OperatorIdentityDecision

    model_config = {"populate_by_name": True}


class SourceHealth(BaseModel):
    provider: ProviderKind
    state: SourceHealthState
    detail: str
    checked_at: datetime = Field(alias="checkedAt")
    last_success_at: datetime | None = Field(default=None, alias="lastSuccessAt")
    next_retry_at: datetime | None = Field(default=None, alias="nextRetryAt")
    complete: bool = False
    failed_operations: list[str] = Field(default_factory=list, alias="failedOperations")

    model_config = {"populate_by_name": True}


class AttributeResolution(BaseModel):
    field: ClaimField
    state: CrossProviderState
    value: str | bool | int | float | None = None
    provider_values: dict[str, str | bool | int | float | None] = Field(
        default_factory=dict, alias="providerValues"
    )
    claim_ids: list[str] = Field(default_factory=list, alias="claimIds")
    explanation: str = ""

    model_config = {"populate_by_name": True}


class UnifiedEntity(BaseModel):
    id: str
    label: str
    category: str = "unknown"
    provider_entity_ids: list[str] = Field(default_factory=list, alias="providerEntityIds")
    providers: list[ProviderKind] = Field(default_factory=list)
    identity_state: CrossProviderState = Field(default="UNKNOWN", alias="identityState")
    attributes: list[AttributeResolution] = Field(default_factory=list)
    evidence_ids: list[str] = Field(default_factory=list, alias="evidenceIds")
    freshness: EvidenceFreshness = "current"

    model_config = {"populate_by_name": True}


class UnifiedRelationship(BaseModel):
    id: str
    subject_id: str = Field(alias="subjectId")
    object_id: str = Field(alias="objectId")
    relationship: str
    state: CrossProviderState
    provider_claim_ids: list[str] = Field(default_factory=list, alias="providerClaimIds")
    explanation: str = ""

    model_config = {"populate_by_name": True}


class UnifiedLabState(BaseModel):
    """The complete public Unified Lab envelope.

    It contains normalized evidence only. Raw provider payloads and raw private
    identifiers are structurally absent.
    """

    generated_at: datetime = Field(alias="generatedAt")
    entities: list[UnifiedEntity] = Field(default_factory=list)
    relationships: list[UnifiedRelationship] = Field(default_factory=list)
    provider_entities: list[ProviderEntity] = Field(default_factory=list, alias="providerEntities")
    claims: list[NormalizedClaim] = Field(default_factory=list)
    identity_links: list[IdentityLink] = Field(default_factory=list, alias="identityLinks")
    conflicts: list[IdentityConflict] = Field(default_factory=list)
    source_health: list[SourceHealth] = Field(default_factory=list, alias="sourceHealth")

    model_config = {"populate_by_name": True}
