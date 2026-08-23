"""Read-only Meraki collection with immediate privacy-aware normalization."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Callable

from .identity_protection import IdentityProtector
from .meraki_client import MerakiApiError, MerakiApiResult, MerakiClient
from .meraki_models import MerakiSelection
from .normalized_evidence import (
    claim,
    normalized_name,
    provenance,
    provider_ref,
    reciprocal_adjacency_token,
    safe_text,
)
from .unified_models import (
    NormalizedClaim,
    ProviderEntity,
    ProviderIdentifier,
    SourceHealth,
)


@dataclass(frozen=True)
class MerakiCollection:
    entities: list[ProviderEntity]
    claims: list[NormalizedClaim]
    source_health: SourceHealth


def _observed_at(value: object, fallback: datetime) -> datetime:
    if isinstance(value, str):
        try:
            parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
            return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)
        except ValueError:
            pass
    return fallback


def _category(product_type: str, model: str) -> str:
    product = product_type.lower()
    upper_model = model.upper()
    if product == "wireless" or upper_model.startswith("MR"):
        return "access-point"
    if product == "appliance" or upper_model.startswith(("MX", "Z")):
        return "security-appliance"
    if product == "switch" or upper_model.startswith("MS"):
        return "switch"
    return "unknown"


class MerakiEvidenceCollector:
    def __init__(
        self,
        client: MerakiClient,
        selection: MerakiSelection,
        *,
        protector: IdentityProtector | None = None,
        now: Callable[[], datetime] | None = None,
    ) -> None:
        self._client = client
        self._selection = selection
        self._protector = protector or IdentityProtector()
        self._now = now or (lambda: datetime.now(timezone.utc))
        self._failures: list[str] = []
        self._rate_limited = False

    def collect(self) -> MerakiCollection:
        collected_at = self._now()
        devices_result = self._fetch(
            "organization_devices",
            path_parameters={"organization_id": self._selection.organization_id},
            query={"networkIds[]": [self._selection.network_id], "perPage": 1000},
            required=True,
        )
        if devices_result is None or not isinstance(devices_result.data, list):
            return MerakiCollection(
                entities=[],
                claims=[],
                source_health=self._health(collected_at, success=False),
            )

        raw_devices = [
            item
            for item in devices_result.data
            if isinstance(item, dict)
            and str(item.get("networkId") or "") == self._selection.network_id
        ]
        availability = self._availability_by_serial(collected_at)
        uplinks = self._uplinks_by_serial()
        appliance_ports = self._list_result(
            "appliance_ports",
            path_parameters={"network_id": self._selection.network_id},
        )

        entities: list[ProviderEntity] = []
        claims: list[NormalizedClaim] = []
        entity_by_serial: dict[str, ProviderEntity] = {}
        for raw in raw_devices:
            entity, entity_claims = self._normalize_device(
                raw,
                availability.get(str(raw.get("serial") or "")),
                uplinks.get(str(raw.get("serial") or ""), []),
                appliance_ports,
                collected_at,
                devices_result.complete,
            )
            if entity is None:
                continue
            entities.append(entity)
            claims.extend(entity_claims)
            serial = str(raw.get("serial") or "")
            if serial:
                entity_by_serial[serial] = entity

        # Per-device adjacency and switch port state are intentionally bounded
        # to devices already assigned to the selected network.
        for raw in raw_devices:
            serial = str(raw.get("serial") or "")
            entity = entity_by_serial.get(serial)
            if not entity:
                continue
            lldp_cdp = self._dict_result(
                "device_lldp_cdp",
                path_parameters={"serial": serial},
            )
            adjacent_entities, adjacency_claims = self._normalize_lldp_cdp(
                entity, raw, lldp_cdp, collected_at
            )
            for adjacent in adjacent_entities:
                if not any(existing.id == adjacent.id for existing in entities):
                    entities.append(adjacent)
            claims.extend(adjacency_claims)
            if entity.category == "switch":
                port_states = self._list_result(
                    "switch_port_statuses",
                    path_parameters={"serial": serial},
                    query={"timespan": 300},
                )
                claims.extend(self._normalize_switch_ports(entity, port_states, collected_at))

        client_items = self._list_result(
            "network_clients",
            path_parameters={"network_id": self._selection.network_id},
            query={"timespan": 3600, "perPage": 1000, "statuses[]": ["Online"]},
        )
        client_entities, client_claims = self._normalize_clients(
            client_items,
            entity_by_serial,
            collected_at,
        )
        entities.extend(client_entities)
        claims.extend(client_claims)

        # Raw provider dictionaries are now out of scope and are never placed
        # in the return value or persistence layer.
        return MerakiCollection(
            entities=entities,
            claims=claims,
            source_health=self._health(collected_at, success=True),
        )

    def _fetch(
        self,
        operation: str,
        *,
        path_parameters: dict[str, str] | None = None,
        query: dict | None = None,
        required: bool = False,
    ) -> MerakiApiResult | None:
        try:
            result = self._client.get(
                operation,
                path_parameters=path_parameters,
                query=query,
            )
        except MerakiApiError as exc:
            self._failures.append(operation)
            self._rate_limited = self._rate_limited or exc.code == "rate-limited"
            if required:
                return None
            return None
        if not result.complete:
            self._failures.append(operation)
            self._rate_limited = self._rate_limited or result.failure_code == "rate-limited"
        return result

    def _list_result(self, operation: str, **kwargs: object) -> list[dict]:
        result = self._fetch(operation, **kwargs)
        return list(result.data) if result and isinstance(result.data, list) else []

    def _dict_result(self, operation: str, **kwargs: object) -> dict:
        result = self._fetch(operation, **kwargs)
        return dict(result.data) if result and isinstance(result.data, dict) else {}

    def _availability_by_serial(self, collected_at: datetime) -> dict[str, dict]:
        items = self._list_result(
            "device_availabilities",
            path_parameters={"organization_id": self._selection.organization_id},
            query={"networkIds[]": [self._selection.network_id], "perPage": 1000},
        )
        return {
            str(item.get("serial")): item
            for item in items
            if item.get("serial") and _observed_at(item.get("lastReportedAt"), collected_at)
        }

    def _uplinks_by_serial(self) -> dict[str, list[dict]]:
        items = self._list_result(
            "appliance_uplinks",
            path_parameters={"organization_id": self._selection.organization_id},
            query={"networkIds[]": [self._selection.network_id], "perPage": 1000},
        )
        return {
            str(item.get("serial")): [value for value in item.get("uplinks", []) if isinstance(value, dict)]
            for item in items
            if item.get("serial")
        }

    def _normalize_device(
        self,
        raw: dict,
        raw_availability: dict | None,
        raw_uplinks: list[dict],
        raw_appliance_ports: list[dict],
        collected_at: datetime,
        complete: bool,
    ) -> tuple[ProviderEntity | None, list[NormalizedClaim]]:
        serial = safe_text(raw.get("serial"), fallback="", limit=128)
        if not serial:
            return None, []
        ref = provider_ref(self._protector, "meraki-dashboard", "device", serial)
        name = safe_text(raw.get("name"), fallback="Unnamed Meraki device")
        model = safe_text(raw.get("model"), fallback="Unknown model", limit=80)
        product_type = safe_text(raw.get("productType"), fallback="unknown", limit=40)
        observed_at = _observed_at(
            (raw_availability or {}).get("lastReportedAt"),
            collected_at,
        )
        source = provenance(
            provider="meraki-dashboard",
            source_kind="organization-device-inventory",
            source_object_ref=ref,
            organization_id=self._selection.organization_id,
            network_id=self._selection.network_id,
            observed_at=observed_at,
            collected_at=collected_at,
            complete=complete,
        )
        identifiers: list[ProviderIdentifier] = []
        protected_serial = self._protector.serial(serial)
        if protected_serial:
            identifiers.append(ProviderIdentifier(
                kind="serial", protectedValue=protected_serial, strength="strong", provenanceRef=ref
            ))
        protected_mac = self._protector.hardware_mac(str(raw.get("mac") or ""))
        if protected_mac:
            identifiers.append(ProviderIdentifier(
                kind="device-mac", protectedValue=protected_mac, strength="strong",
                globallyAdministered=True, provenanceRef=ref,
            ))
        protected_address = self._protector.management_address(str(raw.get("lanIp") or ""))
        if protected_address:
            identifiers.append(ProviderIdentifier(
                kind="management-address", protectedValue=protected_address,
                strength="supporting", provenanceRef=ref,
            ))
        identifiers.extend([
            ProviderIdentifier(
                kind="name", protectedValue=self._protector.protect("name", normalized_name(name)),
                strength="weak", provenanceRef=ref,
            ),
            ProviderIdentifier(
                kind="model", protectedValue=self._protector.protect("model", normalized_name(model)),
                strength="weak", provenanceRef=ref,
            ),
        ])
        entity = ProviderEntity(
            id=ref,
            provider="meraki-dashboard",
            providerRef=ref,
            label=name,
            category=_category(product_type, model),
            vendor="Cisco Meraki",
            model=model,
            identifiers=identifiers,
            observedAt=observed_at,
        )
        entity_claims = [
            claim(provider="meraki-dashboard", subject_ref=ref, field="existence", value=True,
                  strength="strong", provenance_record=source),
            claim(provider="meraki-dashboard", subject_ref=ref, field="name", value=name,
                  strength="weak", provenance_record=source),
            claim(provider="meraki-dashboard", subject_ref=ref, field="model", value=model,
                  strength="supporting", provenance_record=source),
            claim(provider="meraki-dashboard", subject_ref=ref, field="category", value=entity.category,
                  strength="supporting", provenance_record=source),
        ]
        if raw_availability:
            status = safe_text(raw_availability.get("status"), fallback="unknown", limit=32).lower()
            availability_source = source.model_copy(update={"source_kind": "device-availability"})
            entity_claims.append(claim(
                provider="meraki-dashboard", subject_ref=ref, field="availability",
                value=status, strength="supporting", provenance_record=availability_source,
            ))
        for uplink in raw_uplinks:
            interface = safe_text(uplink.get("interface"), fallback="uplink", limit=40)
            status = safe_text(uplink.get("status"), fallback="unknown", limit=32).lower()
            entity_claims.append(claim(
                provider="meraki-dashboard", subject_ref=ref, field="uplink",
                value=f"{interface}:{status}", strength="supporting", provenance_record=source,
                detail="Meraki reports appliance uplink state.",
            ))
        if entity.category == "security-appliance":
            for port in raw_appliance_ports:
                number = safe_text(port.get("number"), fallback="unknown", limit=12)
                context = f"port {number}: {safe_text(port.get('type'), fallback='unknown', limit=20)}"
                if port.get("vlan") is not None:
                    context += f" vlan {safe_text(port.get('vlan'), fallback='unknown', limit=8)}"
                entity_claims.append(claim(
                    provider="meraki-dashboard", subject_ref=ref, field="port", value=context,
                    strength="supporting", provenance_record=source,
                ))
        entity.claim_ids = [item.id for item in entity_claims]
        return entity, entity_claims

    def _normalize_lldp_cdp(
        self,
        entity: ProviderEntity,
        raw_device: dict,
        payload: dict,
        collected_at: datetime,
    ) -> tuple[list[ProviderEntity], list[NormalizedClaim]]:
        adjacent_entities: list[ProviderEntity] = []
        result: list[NormalizedClaim] = []
        ports = payload.get("ports") if isinstance(payload.get("ports"), dict) else {}
        for local_port, observation in ports.items():
            if not isinstance(observation, dict):
                continue
            for protocol in ("lldp", "cdp"):
                neighbor = observation.get(protocol)
                if not isinstance(neighbor, dict):
                    continue
                neighbor_name = safe_text(
                    neighbor.get("systemName") or neighbor.get("deviceId"),
                    fallback="Unidentified neighbour",
                )
                neighbor_port = safe_text(
                    neighbor.get("portId") or neighbor.get("portIdSubtype") or neighbor.get("port"),
                    fallback="unknown",
                    limit=80,
                )
                object_ref = provider_ref(
                    self._protector,
                    "meraki-dashboard",
                    "adjacent-entity",
                    str(neighbor.get("chassisId") or neighbor.get("deviceId") or neighbor_name),
                )
                source = provenance(
                    provider="meraki-dashboard",
                    source_kind=f"device-{protocol}",
                    source_object_ref=entity.provider_ref,
                    organization_id=self._selection.organization_id,
                    network_id=self._selection.network_id,
                    observed_at=collected_at,
                    collected_at=collected_at,
                )
                result.append(claim(
                    provider="meraki-dashboard", subject_ref=entity.id, field="relationship",
                    object_ref=object_ref, value=f"{safe_text(local_port, fallback='unknown', limit=80)}->{neighbor_port}",
                    strength="supporting", provenance_record=source,
                    detail=f"Meraki reports a direct {protocol.upper()} neighbour named {neighbor_name}.",
                ))
                adjacent_identifiers = [ProviderIdentifier(
                    kind="name",
                    protectedValue=self._protector.protect("name", normalized_name(neighbor_name)),
                    strength="weak",
                    provenanceRef=object_ref,
                )]
                chassis = self._protector.hardware_mac(str(neighbor.get("chassisId") or ""))
                if chassis:
                    adjacent_identifiers.append(ProviderIdentifier(
                        kind="chassis-mac", protectedValue=chassis,
                        strength="strong", globallyAdministered=True,
                        provenanceRef=object_ref,
                    ))
                management = self._protector.management_address(
                    str(neighbor.get("managementAddress") or neighbor.get("address") or "")
                )
                if management:
                    adjacent_identifiers.append(ProviderIdentifier(
                        kind="management-address", protectedValue=management,
                        strength="supporting", provenanceRef=object_ref,
                    ))
                adjacent = ProviderEntity(
                    id=object_ref,
                    provider="meraki-dashboard",
                    providerRef=object_ref,
                    label=neighbor_name,
                    category="unknown",
                    identifiers=adjacent_identifiers,
                    observedAt=collected_at,
                )
                existence = claim(
                    provider="meraki-dashboard", subject_ref=object_ref,
                    field="existence", value=True, strength="strong",
                    provenance_record=source,
                    detail=f"Meraki received a direct {protocol.upper()} announcement from this neighbour.",
                )
                adjacent.claim_ids = [existence.id]
                adjacent_entities.append(adjacent)
                result.append(existence)
                token = reciprocal_adjacency_token(
                    self._protector,
                    safe_text(raw_device.get("name"), fallback=entity.label),
                    safe_text(local_port, fallback="unknown", limit=80),
                    neighbor_name,
                    neighbor_port,
                )
                if token:
                    entity.identifiers.append(ProviderIdentifier(
                        kind="reciprocal-adjacency", protectedValue=token,
                        strength="supporting", provenanceRef=entity.provider_ref,
                    ))
                    adjacent.identifiers.append(ProviderIdentifier(
                        kind="reciprocal-adjacency", protectedValue=token,
                        strength="supporting", provenanceRef=object_ref,
                    ))
        return adjacent_entities, result

    def _normalize_switch_ports(
        self,
        entity: ProviderEntity,
        items: list[dict],
        collected_at: datetime,
    ) -> list[NormalizedClaim]:
        source = provenance(
            provider="meraki-dashboard", source_kind="switch-port-status",
            source_object_ref=entity.provider_ref,
            organization_id=self._selection.organization_id,
            network_id=self._selection.network_id,
            observed_at=collected_at, collected_at=collected_at,
        )
        result: list[NormalizedClaim] = []
        for item in items:
            port = safe_text(item.get("portId"), fallback="unknown", limit=40)
            status = safe_text(item.get("status"), fallback="unknown", limit=32)
            result.append(claim(
                provider="meraki-dashboard", subject_ref=entity.id, field="port",
                value=f"port {port}: {status}", strength="supporting", provenance_record=source,
            ))
        return result

    def _normalize_clients(
        self,
        items: list[dict],
        entity_by_serial: dict[str, ProviderEntity],
        collected_at: datetime,
    ) -> tuple[list[ProviderEntity], list[NormalizedClaim]]:
        entities: list[ProviderEntity] = []
        claims: list[NormalizedClaim] = []
        for item in items:
            raw_mac = str(item.get("mac") or "")
            reporter_serial = str(
                item.get("recentDeviceSerial") or item.get("deviceSerial") or ""
            )
            reporter = entity_by_serial.get(reporter_serial)
            if not raw_mac or not reporter:
                continue
            # Client MACs may be randomized and are never durable identity
            # identifiers. They are used once to produce a local opaque sighting.
            token = self._protector.protect("ephemeral-client", raw_mac)
            ref = f"meraki-dashboard:recent-client:{token.rsplit('-', 1)[-1]}"
            observed = _observed_at(item.get("lastSeen"), collected_at)
            source = provenance(
                provider="meraki-dashboard", source_kind="recent-client-attachment",
                source_object_ref=ref,
                organization_id=self._selection.organization_id,
                network_id=self._selection.network_id,
                observed_at=observed, collected_at=collected_at,
            )
            entity = ProviderEntity(
                id=ref, provider="meraki-dashboard", providerRef=ref,
                label=f"Recent client {token[-6:]}", category="client",
                identifiers=[], observedAt=observed,
            )
            attachment = claim(
                provider="meraki-dashboard", subject_ref=ref, field="attachment",
                object_ref=reporter.id, value="recently-observed",
                strength="supporting", provenance_record=source,
                detail="Meraki recently observed this pseudonymous client on the reporting device.",
            )
            entity.claim_ids = [attachment.id]
            entities.append(entity)
            claims.append(attachment)
        return entities, claims

    def _health(self, checked_at: datetime, *, success: bool) -> SourceHealth:
        if not success:
            state = "rate-limited" if self._rate_limited else "unavailable"
            detail = "Meraki inventory collection failed; Catalyst operation is unaffected."
        elif self._failures:
            state = "rate-limited" if self._rate_limited else "partial"
            detail = "Meraki returned partial evidence; missing operations are listed."
        else:
            state = "healthy"
            detail = "Meraki read-only evidence collection completed."
        return SourceHealth(
            provider="meraki-dashboard",
            state=state,
            detail=detail,
            checkedAt=checked_at,
            lastSuccessAt=checked_at if success else None,
            complete=success and not self._failures,
            failedOperations=sorted(set(self._failures)),
        )
