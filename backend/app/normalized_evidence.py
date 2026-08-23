"""Small deterministic helpers shared by provider normalizers."""
from __future__ import annotations

from datetime import datetime
import hashlib
import re

from .identity_protection import IdentityProtector
from .unified_models import (
    EvidenceProvenance,
    EvidenceStrength,
    EvidenceFreshness,
    NormalizedClaim,
    ProviderKind,
    ProviderScope,
)


def safe_text(value: object, *, fallback: str, limit: int = 160) -> str:
    text = " ".join(str(value or "").split())
    text = "".join(char for char in text if ord(char) >= 32)
    return text[:limit] or fallback


def provider_ref(
    protector: IdentityProtector,
    provider: ProviderKind,
    source_kind: str,
    value: str,
) -> str:
    token = protector.protect(f"{provider}:{source_kind}", value)
    return f"{provider}:{source_kind}:{token.rsplit('-', 1)[-1]}"


def stable_claim_id(
    provider: ProviderKind,
    subject_ref: str,
    field: str,
    object_ref: str | None,
    value: object,
) -> str:
    key = f"{provider}|{subject_ref}|{field}|{object_ref or ''}|{value!s}"
    return f"claim-{hashlib.sha256(key.encode('utf-8')).hexdigest()[:20]}"


def provenance(
    *,
    provider: ProviderKind,
    source_kind: str,
    source_object_ref: str,
    observed_at: datetime,
    collected_at: datetime,
    organization_id: str | None = None,
    network_id: str | None = None,
    complete: bool = True,
) -> EvidenceProvenance:
    return EvidenceProvenance(
        provider=provider,
        sourceKind=source_kind,
        sourceObjectRef=source_object_ref,
        scope=ProviderScope(
            organizationId=organization_id,
            networkId=network_id,
            deviceRef=source_object_ref,
        ),
        observedAt=observed_at,
        collectedAt=collected_at,
        complete=complete,
    )


def claim(
    *,
    provider: ProviderKind,
    subject_ref: str,
    field: str,
    value: str | bool | int | float | None,
    strength: EvidenceStrength,
    provenance_record: EvidenceProvenance,
    object_ref: str | None = None,
    freshness: EvidenceFreshness = "current",
    detail: str = "",
) -> NormalizedClaim:
    return NormalizedClaim(
        id=stable_claim_id(provider, subject_ref, field, object_ref, value),
        provider=provider,
        subjectRef=subject_ref,
        field=field,
        value=value,
        objectRef=object_ref,
        strength=strength,
        freshness=freshness,
        provenance=provenance_record,
        detail=detail,
    )


def normalized_name(value: str) -> str:
    return re.sub(r"\s+", " ", value.strip().lower())


def reciprocal_adjacency_token(
    protector: IdentityProtector,
    left_name: str,
    left_port: str,
    right_name: str,
    right_port: str,
) -> str | None:
    endpoints = [
        f"{normalized_name(left_name)}@{normalized_name(left_port)}",
        f"{normalized_name(right_name)}@{normalized_name(right_port)}",
    ]
    if any(endpoint in {"@", "unknown@unknown"} for endpoint in endpoints):
        return None
    return protector.protect("reciprocal-adjacency", "|".join(sorted(endpoints)))
