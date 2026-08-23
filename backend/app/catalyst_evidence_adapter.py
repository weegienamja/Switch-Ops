"""Normalize the existing Catalyst dashboard into Unified Lab contracts."""
from __future__ import annotations

from datetime import datetime, timezone

from .identity_protection import IdentityProtector
from .models import DashboardResponse, LldpNeighbor, NetworkDevice
from .normalized_evidence import (
    claim,
    normalized_name,
    provenance,
    provider_ref,
    reciprocal_adjacency_token,
)
from .unified_models import NormalizedClaim, ProviderEntity, ProviderIdentifier, SourceHealth


def _freshness(value: str) -> str:
    return value if value in {"current", "aging", "stale", "historical"} else "current"


def _lldp_by_interface(dashboard: DashboardResponse) -> dict[str, LldpNeighbor]:
    return {
        neighbor.local_interface: neighbor
        for neighbor in dashboard.discovery.lldp.neighbors
        if neighbor.local_interface
    }


def normalize_catalyst_dashboard(
    dashboard: DashboardResponse,
    *,
    protector: IdentityProtector | None = None,
) -> tuple[list[ProviderEntity], list[NormalizedClaim], SourceHealth]:
    protector = protector or IdentityProtector()
    collected_at = dashboard.topology.generated_at or datetime.now(timezone.utc)
    root_device = next(
        (item for item in dashboard.topology.devices if item.id == dashboard.topology.root_device_id),
        None,
    )
    if root_device is None:
        return [], [], SourceHealth(
            provider="catalyst-ios",
            state="unavailable",
            detail="Catalyst topology did not contain its root device.",
            checkedAt=collected_at,
            complete=False,
            failedOperations=["dashboard-topology"],
        )

    entities: list[ProviderEntity] = []
    claims: list[NormalizedClaim] = []
    entity_by_topology_id: dict[str, ProviderEntity] = {}
    root_ref = provider_ref(protector, "catalyst-ios", "device", root_device.id)
    root_source = provenance(
        provider="catalyst-ios",
        source_kind="ios-dashboard",
        source_object_ref=root_ref,
        observed_at=collected_at,
        collected_at=collected_at,
        complete=not dashboard.section_errors,
    )
    root_identifiers: list[ProviderIdentifier] = []
    protected_serial = protector.serial(dashboard.summary.serial or "")
    if protected_serial:
        root_identifiers.append(ProviderIdentifier(
            kind="serial", protectedValue=protected_serial, strength="strong",
            provenanceRef=root_ref,
        ))
    protected_address = protector.management_address(dashboard.summary.management_ip)
    if protected_address:
        root_identifiers.append(ProviderIdentifier(
            kind="management-address", protectedValue=protected_address,
            strength="supporting", provenanceRef=root_ref,
        ))
    root_identifiers.extend([
        ProviderIdentifier(
            kind="name", protectedValue=protector.protect("name", normalized_name(dashboard.summary.hostname)),
            strength="weak", provenanceRef=root_ref,
        ),
        ProviderIdentifier(
            kind="model", protectedValue=protector.protect("model", normalized_name(dashboard.summary.model)),
            strength="weak", provenanceRef=root_ref,
        ),
    ])
    root = ProviderEntity(
        id=root_ref,
        provider="catalyst-ios",
        providerRef=root_ref,
        label=dashboard.summary.hostname,
        category="switch",
        vendor="Cisco",
        model=dashboard.summary.model,
        identifiers=root_identifiers,
        observedAt=collected_at,
    )
    root_claims = [
        claim(provider="catalyst-ios", subject_ref=root_ref, field="existence", value=True,
              strength="strong", provenance_record=root_source),
        claim(provider="catalyst-ios", subject_ref=root_ref, field="availability", value="online",
              strength="strong", provenance_record=root_source),
        claim(provider="catalyst-ios", subject_ref=root_ref, field="name", value=dashboard.summary.hostname,
              strength="supporting", provenance_record=root_source),
        claim(provider="catalyst-ios", subject_ref=root_ref, field="model", value=dashboard.summary.model,
              strength="supporting", provenance_record=root_source),
        claim(provider="catalyst-ios", subject_ref=root_ref, field="category", value="switch",
              strength="supporting", provenance_record=root_source),
    ]
    root.claim_ids = [item.id for item in root_claims]
    entities.append(root)
    claims.extend(root_claims)
    entity_by_topology_id[root_device.id] = root

    lldp_by_port = _lldp_by_interface(dashboard)
    for device in dashboard.topology.devices:
        if device.id == root_device.id:
            continue
        entity, entity_claims = _normalize_neighbor(
            device,
            dashboard,
            root,
            lldp_by_port.get(device.connected_interface or ""),
            protector,
            collected_at,
        )
        entities.append(entity)
        claims.extend(entity_claims)
        entity_by_topology_id[device.id] = entity

    complete = not dashboard.section_errors
    health = SourceHealth(
        provider="catalyst-ios",
        state="healthy" if complete else "partial",
        detail=(
            "Catalyst evidence collection completed."
            if complete
            else "Catalyst returned partial evidence; its existing views remain available."
        ),
        checkedAt=collected_at,
        lastSuccessAt=collected_at,
        complete=complete,
        failedOperations=sorted(dashboard.section_errors),
    )
    return entities, claims, health


def _normalize_neighbor(
    device: NetworkDevice,
    dashboard: DashboardResponse,
    root: ProviderEntity,
    lldp: LldpNeighbor | None,
    protector: IdentityProtector,
    collected_at: datetime,
) -> tuple[ProviderEntity, list[NormalizedClaim]]:
    ref = provider_ref(protector, "catalyst-ios", "topology-entity", device.id)
    source_kind = device.identity_source if device.identity_source != "none" else "ios-topology"
    source = provenance(
        provider="catalyst-ios",
        source_kind=source_kind,
        source_object_ref=ref,
        observed_at=device.last_seen or collected_at,
        collected_at=collected_at,
        complete=not dashboard.section_errors,
    )
    identifiers: list[ProviderIdentifier] = []
    if device.identity_source not in {"none", "interface-description"} and device.name:
        identifiers.append(ProviderIdentifier(
            kind="name", protectedValue=protector.protect("name", normalized_name(device.name)),
            strength="weak", provenanceRef=ref,
        ))
    if device.model:
        identifiers.append(ProviderIdentifier(
            kind="model", protectedValue=protector.protect("model", normalized_name(device.model)),
            strength="weak", provenanceRef=ref,
        ))
    for address in device.ip_addresses or ([device.ip] if device.ip else []):
        protected = protector.management_address(address)
        if protected:
            identifiers.append(ProviderIdentifier(
                kind="management-address", protectedValue=protected,
                strength="supporting", provenanceRef=ref,
            ))
    if lldp and lldp.chassis_id:
        chassis = protector.hardware_mac(lldp.chassis_id, kind="chassis-mac")
        if chassis:
            identifiers.append(ProviderIdentifier(
                kind="chassis-mac", protectedValue=chassis, strength="strong",
                globallyAdministered=True, provenanceRef=ref,
            ))
    if lldp:
        adjacency = reciprocal_adjacency_token(
            protector,
            dashboard.summary.hostname,
            lldp.local_interface,
            lldp.remote_name,
            lldp.remote_interface or "unknown",
        )
        if adjacency:
            identifiers.append(ProviderIdentifier(
                kind="reciprocal-adjacency", protectedValue=adjacency,
                strength="supporting", provenanceRef=ref,
            ))
    entity = ProviderEntity(
        id=ref,
        provider="catalyst-ios",
        providerRef=ref,
        label=device.name,
        category=device.type,
        vendor=device.vendor,
        model=device.model,
        identifiers=identifiers,
        observedAt=device.last_seen or collected_at,
        freshness=_freshness(device.freshness),
    )
    existence_strength = "strong" if device.evidence_level == "direct" else "supporting"
    result = [
        claim(provider="catalyst-ios", subject_ref=ref, field="existence", value=True,
              strength=existence_strength, provenance_record=source, freshness=entity.freshness),
        claim(provider="catalyst-ios", subject_ref=ref, field="name", value=device.name,
              strength="weak", provenance_record=source, freshness=entity.freshness),
        claim(provider="catalyst-ios", subject_ref=ref, field="category", value=device.type,
              strength="supporting", provenance_record=source, freshness=entity.freshness),
    ]
    if device.model:
        result.append(claim(
            provider="catalyst-ios", subject_ref=ref, field="model", value=device.model,
            strength="supporting", provenance_record=source, freshness=entity.freshness,
        ))
    if device.connected_interface:
        result.extend([
            claim(
                provider="catalyst-ios", subject_ref=ref, field="attachment",
                object_ref=root.id, value=device.connected_interface,
                strength=existence_strength, provenance_record=source,
                freshness=entity.freshness,
            ),
            claim(
                provider="catalyst-ios", subject_ref=root.id, field="relationship",
                object_ref=ref, value=device.connected_interface,
                strength=existence_strength, provenance_record=source,
                freshness=entity.freshness,
            ),
        ])
    entity.claim_ids = [item.id for item in result]
    return entity, result
