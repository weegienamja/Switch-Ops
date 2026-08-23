"""Cross-provider reconciliation without provider precedence."""
from __future__ import annotations

from collections import defaultdict
from datetime import datetime, timezone
import hashlib

from .identity_resolution import resolve_cross_provider_identities
from .unified_models import (
    AttributeResolution,
    IdentityConflict,
    IdentityLink,
    IdentityReason,
    NormalizedClaim,
    ProviderEntity,
    SourceHealth,
    UnifiedEntity,
    UnifiedLabState,
    UnifiedRelationship,
)


_ATTRIBUTE_FIELDS = (
    "attachment",
    "relationship",
    "availability",
    "name",
    "model",
    "vlan",
    "port",
)


def _id(prefix: str, *values: str) -> str:
    digest = hashlib.sha256("|".join(sorted(values)).encode("utf-8")).hexdigest()[:20]
    return f"{prefix}-{digest}"


def _components(entities: list[ProviderEntity], links: list[IdentityLink]) -> list[list[str]]:
    parents = {entity.id: entity.id for entity in entities}

    def find(value: str) -> str:
        while parents[value] != value:
            parents[value] = parents[parents[value]]
            value = parents[value]
        return value

    def union(left: str, right: str) -> None:
        a, b = find(left), find(right)
        if a != b:
            parents[max(a, b)] = min(a, b)

    for link in links:
        if link.state == "confirmed":
            union(link.left_entity_id, link.right_entity_id)
    grouped: dict[str, list[str]] = defaultdict(list)
    for entity in entities:
        grouped[find(entity.id)].append(entity.id)
    return [sorted(values) for _, values in sorted(grouped.items())]


def _resolve_attribute(
    field: str,
    member_ids: set[str],
    claims: list[NormalizedClaim],
) -> AttributeResolution:
    relevant = [
        item for item in claims
        if item.subject_ref in member_ids and item.field == field and item.value is not None
    ]
    if not relevant:
        return AttributeResolution(field=field, state="UNKNOWN", explanation="No provider supplied this claim.")
    if all(item.freshness in {"stale", "historical"} for item in relevant):
        return AttributeResolution(
            field=field, state="STALE", claimIds=[item.id for item in relevant],
            explanation="Only stale or historical provider evidence is available.",
        )
    provider_values: dict[str, set[str]] = defaultdict(set)
    raw_values: dict[str, str | bool | int | float | None] = {}
    for item in relevant:
        key = str(item.value)
        provider_values[item.provider].add(key)
        raw_values[key] = item.value
    flattened = {provider: ", ".join(sorted(values)) for provider, values in provider_values.items()}
    distinct = set().union(*provider_values.values())
    if any(len(values) > 1 for values in provider_values.values()):
        state = "AMBIGUOUS"
        explanation = "At least one provider supplied multiple current values."
        value = None
    elif len(provider_values) == 1:
        state = "PROVIDER_ONLY"
        explanation = "Only one provider supplied this claim."
        value = raw_values[next(iter(distinct))]
    elif len(distinct) == 1:
        state = "AGREED"
        explanation = "The providers report the same current value."
        value = raw_values[next(iter(distinct))]
    else:
        state = "CONFLICT"
        explanation = "The providers report different current values; neither wins automatically."
        value = None
    return AttributeResolution(
        field=field,
        state=state,
        value=value,
        providerValues=flattened,
        claimIds=[item.id for item in relevant],
        explanation=explanation,
    )


def reconcile_unified_state(
    provider_entities: list[ProviderEntity],
    claims: list[NormalizedClaim],
    source_health: list[SourceHealth],
    *,
    identity_overrides: dict[str, tuple[str, datetime]] | None = None,
    generated_at: datetime | None = None,
) -> UnifiedLabState:
    generated_at = generated_at or datetime.now(timezone.utc)
    links, conflicts = resolve_cross_provider_identities(
        provider_entities, evaluated_at=generated_at
    )
    identity_overrides = identity_overrides or {}
    for link in links:
        override = identity_overrides.get(link.id)
        if not override:
            continue
        decision, decided_at = override
        # A local operator may confirm/reject ambiguity, but can never waive a
        # retained strong identifier conflict through this interface.
        if link.state == "conflicted":
            continue
        if decision == "confirm":
            link.state = "confirmed"
        elif decision == "reject":
            link.state = "rejected"
        else:
            continue
        link.automatic = False
        link.decided_at = decided_at
        link.reasons.append(IdentityReason(
            kind="operator",
            field="operator-decision",
            strength="supporting",
            summary=f"The local operator chose to {decision} this ambiguous identity relationship.",
            provenanceRefs=[],
        ))

    by_id = {entity.id: entity for entity in provider_entities}
    conflict_entity_ids = {
        entity_id
        for conflict in conflicts
        for entity_id in (conflict.left_entity_id, conflict.right_entity_id)
    }
    candidate_entity_ids = {
        entity_id
        for link in links if link.state == "candidate"
        for entity_id in (link.left_entity_id, link.right_entity_id)
    }
    unified: list[UnifiedEntity] = []
    provider_to_unified: dict[str, str] = {}
    for component in _components(provider_entities, links):
        members = [by_id[item] for item in component]
        providers = sorted({item.provider for item in members})
        names = {item.label for item in members}
        categories = {item.category for item in members}
        unified_id = _id("unified-entity", *component)
        if len(providers) > 1:
            identity_state = "AGREED"
        elif any(item in conflict_entity_ids for item in component):
            identity_state = "CONFLICT"
        elif any(item in candidate_entity_ids for item in component):
            identity_state = "AMBIGUOUS"
        else:
            identity_state = "PROVIDER_ONLY"
        label = next(iter(names)) if len(names) == 1 else f"Unified device {unified_id[-6:]}"
        entity = UnifiedEntity(
            id=unified_id,
            label=label,
            category=next(iter(categories)) if len(categories) == 1 else "unknown",
            providerEntityIds=component,
            providers=providers,
            identityState=identity_state,
            attributes=[
                AttributeResolution(
                    field="identity",
                    state=identity_state,
                    explanation=(
                        "Strong provider identifiers or a local operator decision join these records."
                        if identity_state == "AGREED"
                        else "A strong identifier conflict prevents a merge."
                        if identity_state == "CONFLICT"
                        else "Correlation evidence is ambiguous and the records remain separate."
                        if identity_state == "AMBIGUOUS"
                        else "This identity is currently reported by one provider only."
                    ),
                ),
                *[
                    _resolve_attribute(field, set(component), claims)
                    for field in _ATTRIBUTE_FIELDS
                ],
            ],
            evidenceIds=sorted({claim_id for item in members for claim_id in item.claim_ids}),
            freshness=(
                "stale" if all(item.freshness in {"stale", "historical"} for item in members)
                else "current"
            ),
        )
        unified.append(entity)
        for item in component:
            provider_to_unified[item] = unified_id

    relationships = _relationships(claims, provider_to_unified)
    return UnifiedLabState(
        generatedAt=generated_at,
        entities=unified,
        relationships=relationships,
        providerEntities=provider_entities,
        claims=claims,
        identityLinks=links,
        conflicts=conflicts,
        sourceHealth=source_health,
    )


def _relationships(
    claims: list[NormalizedClaim],
    provider_to_unified: dict[str, str],
) -> list[UnifiedRelationship]:
    grouped: dict[tuple[str, str, str], list[NormalizedClaim]] = defaultdict(list)
    for item in claims:
        if item.field not in {"attachment", "relationship"} or not item.object_ref:
            continue
        subject = provider_to_unified.get(item.subject_ref)
        object_id = provider_to_unified.get(item.object_ref)
        if not subject or not object_id or subject == object_id:
            continue
        grouped[(subject, object_id, item.field)].append(item)
    result: list[UnifiedRelationship] = []
    for (subject, object_id, relationship), items in sorted(grouped.items()):
        providers = {item.provider for item in items}
        values = {str(item.value) for item in items}
        if all(item.freshness in {"stale", "historical"} for item in items):
            state = "STALE"
        elif len(providers) == 1:
            state = "PROVIDER_ONLY"
        elif len(values) == 1:
            state = "AGREED"
        else:
            state = "CONFLICT"
        result.append(UnifiedRelationship(
            id=_id("unified-link", subject, object_id, relationship),
            subjectId=subject,
            objectId=object_id,
            relationship=relationship,
            state=state,
            providerClaimIds=[item.id for item in items],
            explanation=(
                "Providers agree on this relationship." if state == "AGREED"
                else "This relationship is supported by one provider only." if state == "PROVIDER_ONLY"
                else "Provider relationship evidence is stale." if state == "STALE"
                else "Providers disagree about this relationship."
            ),
        ))
    return result
