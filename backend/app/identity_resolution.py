"""Conservative deterministic cross-provider identity resolution."""
from __future__ import annotations

from datetime import datetime, timezone
import hashlib

from .unified_models import (
    IdentityConflict,
    IdentityLink,
    IdentityReason,
    ProviderEntity,
    ProviderIdentifier,
)


_SUPPORTING_KINDS = {
    "management-address",
    "direct-adjacency",
    "reciprocal-adjacency",
}
_WEAK_KINDS = {"name", "model"}


def _stable_id(prefix: str, *values: str) -> str:
    digest = hashlib.sha256("|".join(sorted(values)).encode("utf-8")).hexdigest()[:20]
    return f"{prefix}-{digest}"


def _by_kind(entity: ProviderEntity) -> dict[str, list[ProviderIdentifier]]:
    result: dict[str, list[ProviderIdentifier]] = {}
    for identifier in entity.identifiers:
        # A randomized/local MAC must never participate even if a buggy caller
        # manages to construct one of these models.
        if identifier.kind in {"chassis-mac", "device-mac"} and not identifier.globally_administered:
            continue
        result.setdefault(identifier.kind, []).append(identifier)
    return result


def resolve_identity_pair(
    left: ProviderEntity,
    right: ProviderEntity,
    *,
    evaluated_at: datetime | None = None,
) -> tuple[IdentityLink, list[IdentityConflict]]:
    """Resolve two provider-local entities without fuzzy or silent merging.

    Rules, in order:
    1. Different providers only.
    2. Any comparable strong identifier disagreement is a conflict and merges
       nothing, even if names or addresses agree.
    3. One exact protected serial or global hardware/chassis MAC confirms.
    4. Supporting observations and weak labels produce a visible candidate.
       They never automatically merge entities.
    5. With no correlation evidence, no link is proposed (``rejected``).
    """
    evaluated_at = evaluated_at or datetime.now(timezone.utc)
    link_id = _stable_id("identity-link", left.id, right.id)
    if left.provider == right.provider:
        return (
            IdentityLink(
                id=link_id,
                leftEntityId=left.id,
                rightEntityId=right.id,
                state="rejected",
                reasons=[],
                evaluatedAt=evaluated_at,
            ),
            [],
        )

    left_kinds = _by_kind(left)
    right_kinds = _by_kind(right)
    reasons: list[IdentityReason] = []
    conflicts: list[IdentityConflict] = []
    strong_agreements = 0
    strong_disagreements: list[tuple[str, list[ProviderIdentifier]]] = []
    supporting_agreements = 0
    weak_agreements = 0

    # Chassis and device MACs are the same hardware-identity family. One
    # provider may call an LLDP chassis ID a chassis MAC while its inventory
    # calls the exact address the device MAC.
    strong_groups: tuple[tuple[str, tuple[str, ...]], ...] = (
        ("serial", ("serial",)),
        ("chassis-mac", ("chassis-mac", "device-mac")),
    )
    for kind, member_kinds in strong_groups:
        left_items = [item for member in member_kinds for item in left_kinds.get(member, [])]
        right_items = [item for member in member_kinds for item in right_kinds.get(member, [])]
        if not left_items or not right_items:
            continue
        left_values = {item.protected_value for item in left_items}
        right_values = {item.protected_value for item in right_items}
        common = left_values & right_values
        provenance = [item.provenance_ref for item in left_items + right_items]
        if common:
            strong_agreements += 1
            reasons.append(
                IdentityReason(
                    kind="agreement",
                    field=kind,
                    strength="strong",
                    summary=f"Both providers report the same protected {kind}.",
                    provenanceRefs=provenance,
                )
            )
        else:
            strong_disagreements.append((kind, left_items + right_items))

    for kind in sorted(_SUPPORTING_KINDS | _WEAK_KINDS):
        left_items = left_kinds.get(kind, [])
        right_items = right_kinds.get(kind, [])
        if not left_items or not right_items:
            continue
        common = {item.protected_value for item in left_items} & {
            item.protected_value for item in right_items
        }
        if not common:
            continue
        strength = "supporting" if kind in _SUPPORTING_KINDS else "weak"
        if strength == "supporting":
            supporting_agreements += 1
        else:
            weak_agreements += 1
        reasons.append(
            IdentityReason(
                kind="support" if strength == "supporting" else "hint",
                field=kind,
                strength=strength,
                summary=(
                    f"Both providers report the same protected {kind}; "
                    "this is not sufficient for an automatic merge."
                ),
                provenanceRefs=[item.provenance_ref for item in left_items + right_items],
            )
        )

    # A pair of unrelated, serialised devices is not a conflict merely because
    # their serials differ. A strong disagreement becomes meaningful only when
    # some other evidence first makes the pair a plausible identity candidate.
    name_agreement = any(
        reason.field == "name" and reason.kind == "hint" for reason in reasons
    )
    plausible_pair = bool(strong_agreements or supporting_agreements or name_agreement)
    if strong_disagreements and plausible_pair:
        for kind, items in strong_disagreements:
            conflict = IdentityConflict(
                id=_stable_id("identity-conflict", left.id, right.id, kind),
                leftEntityId=left.id,
                rightEntityId=right.id,
                field=kind,
                summary=f"The providers report different strong {kind} identifiers.",
                provenanceRefs=[item.provenance_ref for item in items],
            )
            conflicts.append(conflict)
            reasons.append(
                IdentityReason(
                    kind="conflict",
                    field=kind,
                    strength="strong",
                    summary=conflict.summary,
                    provenanceRefs=conflict.provenance_refs,
                )
            )
        return (
            IdentityLink(
                id=link_id,
                leftEntityId=left.id,
                rightEntityId=right.id,
                state="conflicted",
                reasons=reasons,
                evaluatedAt=evaluated_at,
            ),
            conflicts,
        )

    if strong_agreements:
        state = "confirmed"
    elif supporting_agreements or weak_agreements:
        state = "candidate"
    else:
        state = "rejected"

    return (
        IdentityLink(
            id=link_id,
            leftEntityId=left.id,
            rightEntityId=right.id,
            state=state,
            reasons=reasons,
            evaluatedAt=evaluated_at,
        ),
        [],
    )


def resolve_cross_provider_identities(
    entities: list[ProviderEntity],
    *,
    evaluated_at: datetime | None = None,
) -> tuple[list[IdentityLink], list[IdentityConflict]]:
    """Return only meaningful cross-provider candidates, confirmations/conflicts."""
    links: list[IdentityLink] = []
    conflicts: list[IdentityConflict] = []
    ordered = sorted(entities, key=lambda entity: (entity.provider, entity.id))
    strong_counts: dict[tuple[str, str, str], int] = {}
    for entity in ordered:
        for family, values in _strong_tokens(entity).items():
            for value in values:
                key = (entity.provider, family, value)
                strong_counts[key] = strong_counts.get(key, 0) + 1
    for index, left in enumerate(ordered):
        for right in ordered[index + 1 :]:
            if left.provider == right.provider:
                continue
            link, pair_conflicts = resolve_identity_pair(
                left,
                right,
                evaluated_at=evaluated_at,
            )
            if link.state == "confirmed":
                left_tokens = _strong_tokens(left)
                right_tokens = _strong_tokens(right)
                shared_families = [
                    family
                    for family in set(left_tokens) & set(right_tokens)
                    if left_tokens[family] & right_tokens[family]
                ]
                unique_agreement = any(
                    strong_counts.get((left.provider, family, value), 0) == 1
                    and strong_counts.get((right.provider, family, value), 0) == 1
                    for family in set(left_tokens) & set(right_tokens)
                    for value in left_tokens[family] & right_tokens[family]
                )
                if not unique_agreement:
                    link.state = "candidate"
                    link.reasons.append(IdentityReason(
                        kind="support",
                        field=(
                            "chassis-mac"
                            if "hardware-mac" in shared_families
                            else "serial"
                        ),
                        strength="strong",
                        summary=(
                            "The protected strong identifier is not unique within a provider, "
                            "so SwitchOps will not merge these records automatically."
                        ),
                        provenanceRefs=[],
                    ))
            if link.state != "rejected":
                links.append(link)
            conflicts.extend(pair_conflicts)
    return links, conflicts


def _strong_tokens(entity: ProviderEntity) -> dict[str, set[str]]:
    grouped = _by_kind(entity)
    return {
        "serial": {item.protected_value for item in grouped.get("serial", [])},
        "hardware-mac": {
            item.protected_value
            for kind in ("chassis-mac", "device-mac")
            for item in grouped.get(kind, [])
        },
    }
