"""Deterministic, non-executable management-path recovery planning."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
import hashlib
import ipaddress
import json
from typing import Any, Literal

from pydantic import BaseModel, Field

from .meraki_management import MerakiManagementEvidence
from .recovery_capability import dhcp_coexistence_validated
from .recovery_execution import (
    RecoveryExecutionArchitecture,
    build_planning_architecture,
)


PlanStatus = Literal["NOT_NEEDED", "BLOCKED", "READY", "NOT_SUPPORTED"]
RecoveryKind = Literal["NONE", "TEMPORARY_SECONDARY_IPV4"]
BlockerCode = Literal[
    "DIAGNOSIS_NOT_ADDRESS_RECOVERABLE",
    "VALID_MANAGEMENT_PATH_ALREADY_EXISTS",
    "COMPATIBLE_SECONDARY_ADDRESS_PRESENT",
    "HISTORICAL_MANAGEMENT_PREFIX_MISSING",
    "HISTORICAL_EVIDENCE_STALE",
    "MANAGEMENT_ADAPTER_UNVERIFIED",
    "DHCP_STATIC_COEXISTENCE_DISABLED",
    "DHCP_STATIC_COEXISTENCE_UNVERIFIED",
    "COLLISION_SAFE_ADDRESS_UNAVAILABLE",
    "CANDIDATE_ADDRESS_INVALID",
    "CURRENT_OBSERVATION_STALE",
]


class RecoveryBlocker(BaseModel):
    code: BlockerCode
    summary: str


class CandidateAddressEvidence(BaseModel):
    address: str
    prefix_length: int = Field(alias="prefixLength", ge=1, le=30)
    assurance: Literal["authoritative-reservation", "unverified"]
    source: str = Field(min_length=1, max_length=80)
    observed_at: datetime = Field(alias="observedAt")

    model_config = {"populate_by_name": True}


class RecoveryOperation(BaseModel):
    kind: RecoveryKind
    adapter_id: str | None = Field(default=None, alias="adapterId")
    candidate_address: str | None = Field(default=None, alias="candidateAddress")
    prefix_length: int | None = Field(default=None, alias="prefixLength")
    gateway: None = None
    expected_route: str | None = Field(default=None, alias="expectedRoute")
    persistence: Literal["temporary-active-store"] = "temporary-active-store"

    model_config = {"populate_by_name": True}


class RecoveryPlanBinding(BaseModel):
    schema_version: int = Field(default=1, alias="schemaVersion")
    target_id: str = Field(alias="targetId")
    adapter_id: str | None = Field(default=None, alias="adapterId")
    primary_address: str | None = Field(default=None, alias="primaryAddress")
    prefix_length: int | None = Field(default=None, alias="prefixLength")
    default_gateway: str | None = Field(default=None, alias="defaultGateway")
    dhcp_lease_obtained: datetime | None = Field(
        default=None, alias="dhcpLeaseObtained"
    )
    dhcp_static_coexistence: bool | None = Field(
        default=None, alias="dhcpStaticCoexistence"
    )
    route_fingerprint: str = Field(alias="routeFingerprint")
    diagnosis: str
    evidence_observed_at: datetime = Field(alias="evidenceObservedAt")
    state_fingerprint: str = Field(alias="stateFingerprint")

    model_config = {"populate_by_name": True}


class RecoveryPlan(BaseModel):
    plan_id: str = Field(alias="planId")
    generated_at: datetime = Field(alias="generatedAt")
    status: PlanStatus
    kind: RecoveryKind
    headline: str
    summary: str
    blockers: list[RecoveryBlocker] = Field(default_factory=list)
    missing_evidence: list[str] = Field(default_factory=list, alias="missingEvidence")
    warnings: list[str] = Field(default_factory=list)
    operation: RecoveryOperation
    candidate_evidence: CandidateAddressEvidence | None = Field(
        default=None, alias="candidateEvidence"
    )
    expected_effect: list[str] = Field(default_factory=list, alias="expectedEffect")
    unchanged_state: list[str] = Field(default_factory=list, alias="unchangedState")
    verification_steps: list[str] = Field(
        default_factory=list, alias="verificationSteps"
    )
    rollback_steps: list[str] = Field(default_factory=list, alias="rollbackSteps")
    binding: RecoveryPlanBinding
    execution_architecture: RecoveryExecutionArchitecture = Field(
        alias="executionArchitecture"
    )
    execution_enabled: bool = Field(default=False, alias="executionEnabled")

    model_config = {"populate_by_name": True}


class RecoveryPlanValidation(BaseModel):
    valid: bool
    changed_fields: list[str] = Field(default_factory=list, alias="changedFields")

    model_config = {"populate_by_name": True}


def _target_id(target: str) -> str:
    return "target-" + hashlib.sha256(target.casefold().encode("utf-8")).hexdigest()[:24]


def _route_fingerprint(current: Any) -> str:
    route = current.route
    payload = {
        "adapterId": current.adapter_id,
        "interfaceIndex": current.interface_index,
        "interfaceMetric": getattr(current, "interface_metric", None),
        "destinationPrefix": route.destination_prefix,
        "nextHop": route.next_hop,
        "kind": route.kind,
        "routeMetric": getattr(route, "route_metric", None),
    }
    return hashlib.sha256(
        json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()[:24]


def _binding_payload(target: str, current: Any, diagnosis: Any) -> dict[str, Any]:
    return {
        "targetId": _target_id(target),
        "adapterId": current.adapter_id,
        "primaryAddress": current.source_ip,
        "prefixLength": current.prefix_length,
        "defaultGateway": current.default_gateway,
        "dhcpLeaseObtained": (
            current.dhcp_lease_obtained.isoformat()
            if current.dhcp_lease_obtained
            else None
        ),
        "dhcpStaticCoexistence": getattr(
            current, "dhcp_static_coexistence", None
        ),
        "routeFingerprint": _route_fingerprint(current),
        "diagnosis": diagnosis.conclusion,
    }


def build_binding(target: str, current: Any, diagnosis: Any) -> RecoveryPlanBinding:
    payload = _binding_payload(target, current, diagnosis)
    fingerprint = hashlib.sha256(
        json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()
    return RecoveryPlanBinding(
        **payload,
        evidenceObservedAt=current.observed_at,
        stateFingerprint=fingerprint,
    )


def validate_recovery_plan_binding(
    plan: RecoveryPlan,
    *,
    target: str,
    current: Any,
    diagnosis: Any,
) -> RecoveryPlanValidation:
    actual = build_binding(target, current, diagnosis)
    expected = plan.binding
    comparisons = {
        "target": expected.target_id == actual.target_id,
        "adapter": expected.adapter_id == actual.adapter_id,
        "primary_address": expected.primary_address == actual.primary_address,
        "prefix": expected.prefix_length == actual.prefix_length,
        "default_gateway": expected.default_gateway == actual.default_gateway,
        "dhcp_lease": expected.dhcp_lease_obtained == actual.dhcp_lease_obtained,
        "dhcp_static_coexistence": (
            expected.dhcp_static_coexistence == actual.dhcp_static_coexistence
        ),
        "route": expected.route_fingerprint == actual.route_fingerprint,
        "diagnosis": expected.diagnosis == actual.diagnosis,
        "evidence_observed_at": (
            expected.evidence_observed_at == actual.evidence_observed_at
        ),
    }
    changed = [field for field, unchanged in comparisons.items() if not unchanged]
    return RecoveryPlanValidation(valid=not changed, changedFields=changed)


def _network(value: str | None) -> ipaddress.IPv4Network | None:
    if not value:
        return None
    try:
        parsed = ipaddress.ip_network(value, strict=False)
        return parsed if isinstance(parsed, ipaddress.IPv4Network) else None
    except ValueError:
        return None


def _plan_id(
    binding: RecoveryPlanBinding,
    operation: RecoveryOperation,
    candidate: CandidateAddressEvidence | None,
) -> str:
    payload = {
        "binding": binding.model_dump(by_alias=True, mode="json"),
        "operation": operation.model_dump(by_alias=True, mode="json"),
        "candidateEvidence": (
            candidate.model_dump(by_alias=True, mode="json") if candidate else None
        ),
    }
    return "recovery-plan-" + hashlib.sha256(
        json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()[:20]


def build_recovery_plan(
    *,
    target: str,
    current: Any,
    last_known_good: Any | None,
    diagnosis: Any,
    meraki: MerakiManagementEvidence,
    candidate: CandidateAddressEvidence | None = None,
    now: datetime | None = None,
) -> RecoveryPlan:
    """Create a state-bound explanation; this function has no I/O or mutation."""
    generated_at = now or datetime.now(timezone.utc)
    binding = build_binding(target, current, diagnosis)
    blockers: list[RecoveryBlocker] = []
    missing: list[str] = []
    warnings = [
        "Current Catalyst control-plane state remains unverified.",
        "A future executor must re-observe and validate every binding before applying anything.",
    ]

    def result(
        status: PlanStatus,
        kind: RecoveryKind,
        headline: str,
        summary: str,
        operation: RecoveryOperation | None = None,
    ) -> RecoveryPlan:
        selected = operation or RecoveryOperation(kind="NONE")
        selected_candidate = candidate if kind == "TEMPORARY_SECONDARY_IPV4" else None
        return RecoveryPlan(
            planId=_plan_id(binding, selected, selected_candidate),
            generatedAt=generated_at,
            status=status,
            kind=kind,
            headline=headline,
            summary=summary,
            blockers=blockers,
            missingEvidence=missing,
            warnings=warnings,
            operation=selected,
            candidateEvidence=selected_candidate,
            expectedEffect=(
                [
                    "Create an on-link route for the historical management prefix on the bound adapter.",
                    "Allow source selection from the temporary management address for the Catalyst target.",
                ]
                if kind == "TEMPORARY_SECONDARY_IPV4"
                else []
            ),
            unchangedState=(
                [
                    "Keep the DHCP primary address and current default gateway.",
                    "Do not modify Catalyst or Meraki configuration.",
                    "Do not add a gateway for the temporary address.",
                    "Do not alter DHCP/static coexistence as part of this operation.",
                ]
                if kind == "TEMPORARY_SECONDARY_IPV4"
                else []
            ),
            verificationSteps=(
                [
                    "Re-observe the bound adapter and confirm its DHCP primary address is unchanged.",
                    "Confirm the temporary address reaches Preferred state without duplicate-address detection failure.",
                    "Confirm the historical management prefix has an on-link route on the bound adapter.",
                    "Confirm the default route and gateway are unchanged.",
                    "Probe Catalyst TCP/22 using the temporary source address.",
                    "Reconnect the serialized SSH session and perform one bounded read-only observation.",
                ]
                if kind == "TEMPORARY_SECONDARY_IPV4"
                else []
            ),
            rollbackSteps=(
                [
                    (
                        "Remove only the exact journal-owned address object "
                        f"{selected.candidate_address}/{selected.prefix_length} on "
                        f"adapter {selected.adapter_id}; require operator reconciliation "
                        "if its post-apply fingerprint no longer matches."
                    ),
                    "Re-observe the primary address, default route, and Internet connectivity.",
                ]
                if kind == "TEMPORARY_SECONDARY_IPV4"
                else []
            ),
            binding=binding,
            executionArchitecture=build_planning_architecture(
                plan_status=status,
                blocker_codes=[blocker.code for blocker in blockers],
            ),
            executionEnabled=False,
        )

    if diagnosis.conclusion == "MANAGEMENT_PATH_HEALTHY":
        return result(
            "NOT_NEEDED",
            "NONE",
            "No recovery needed",
            "The bounded TCP/22 probe and serialized Catalyst session are healthy.",
        )
    if diagnosis.conclusion in {
        "AUTHENTICATION_FAILED",
        "HOST_KEY_CHANGED",
        "SSH_NEGOTIATION_FAILED",
        "SSH_SERVICE_UNAVAILABLE",
    }:
        blockers.append(
            RecoveryBlocker(
                code="DIAGNOSIS_NOT_ADDRESS_RECOVERABLE",
                summary="The diagnosed failure is not repaired by adding a host address.",
            )
        )
        return result(
            "NOT_SUPPORTED",
            "NONE",
            "Address recovery is not applicable",
            "SwitchOps will not propose a network-address change for this failure class.",
        )
    if current.target_on_connected_prefix or current.route.kind in {"connected", "scoped"}:
        blockers.append(
            RecoveryBlocker(
                code="VALID_MANAGEMENT_PATH_ALREADY_EXISTS",
                summary="Windows already has a connected or explicit route for the target.",
            )
        )
        return result(
            "NOT_SUPPORTED",
            "NONE",
            "Host address recovery is inappropriate",
            "The current host route is already valid; device, service, filtering, or Layer-2 evidence is required.",
        )
    if diagnosis.conclusion not in {"HOST_NETWORK_CHANGED", "HOST_PATH_DEGRADED"}:
        blockers.append(
            RecoveryBlocker(
                code="DIAGNOSIS_NOT_ADDRESS_RECOVERABLE",
                summary="The available diagnosis does not support an address recovery.",
            )
        )
        return result(
            "BLOCKED",
            "NONE",
            "Recovery plan blocked",
            "SwitchOps needs a supported host-network-change diagnosis before proposing an address operation.",
        )

    historical_prefix = _network(
        (last_known_good.connected_prefix or last_known_good.management_prefix)
        if last_known_good
        else None
    )
    compatible_addresses = []
    if historical_prefix is not None:
        for item in getattr(current, "adapter_addresses", []):
            try:
                address = ipaddress.ip_address(item.address)
            except ValueError:
                continue
            if (
                isinstance(address, ipaddress.IPv4Address)
                and address in historical_prefix
                and item.prefix_length == historical_prefix.prefixlen
                and str(getattr(item, "address_state", "") or "").casefold()
                == "preferred"
                and getattr(item, "skip_as_source", None) is not True
            ):
                compatible_addresses.append(str(address))
    if compatible_addresses:
        blockers.append(
            RecoveryBlocker(
                code="COMPATIBLE_SECONDARY_ADDRESS_PRESENT",
                summary="The selected adapter already has a usable address in the historical management prefix.",
            )
        )
        return result(
            "NOT_SUPPORTED",
            "NONE",
            "Existing management address requires path diagnosis",
            "SwitchOps will not propose a duplicate address; route, VLAN, filtering, and device evidence must be investigated.",
        )
    if historical_prefix is None:
        blockers.append(
            RecoveryBlocker(
                code="HISTORICAL_MANAGEMENT_PREFIX_MISSING",
                summary="No validated historical IPv4 management prefix is available.",
            )
        )
    if last_known_good and generated_at - last_known_good.observed_at > timedelta(days=7):
        blockers.append(
            RecoveryBlocker(
                code="HISTORICAL_EVIDENCE_STALE",
                summary="The historical management-path evidence is older than seven days.",
            )
        )
    if not last_known_good or last_known_good.same_adapter_as_current is not True:
        blockers.append(
            RecoveryBlocker(
                code="MANAGEMENT_ADAPTER_UNVERIFIED",
                summary="The currently selected adapter is not proven to be the historical management adapter.",
            )
        )
    if generated_at - current.observed_at > timedelta(minutes=2):
        blockers.append(
            RecoveryBlocker(
                code="CURRENT_OBSERVATION_STALE",
                summary="The current Windows observation is too old for a bound recovery plan.",
            )
        )
    # The Windows DHCP/static coexistence setting governs the *standard*
    # configuration API, which replaces an interface's addressing and so would
    # disable a working DHCP lease. It has no bearing on the primitive SwitchOps
    # would actually use once that primitive is shown to add a separate unicast
    # row and leave the lease intact. Measured on a disposable DHCP adapter: the
    # primary stayed DHCP/DHCP and Preferred with a finite lease still counting
    # down. Until that is measured, the setting is still the safest signal
    # available and continues to block.
    coexistence = getattr(current, "dhcp_static_coexistence", None)
    if current.dhcp_enabled and not dhcp_coexistence_validated():
        if coexistence is False:
            blockers.append(
                RecoveryBlocker(
                    code="DHCP_STATIC_COEXISTENCE_DISABLED",
                    summary="Adding a static address with the standard API would disable the working DHCP configuration.",
                )
            )
        elif coexistence is not True:
            blockers.append(
                RecoveryBlocker(
                    code="DHCP_STATIC_COEXISTENCE_UNVERIFIED",
                    summary="DHCP/static address coexistence has not been verified on the bound adapter.",
                )
            )

    candidate_address: str | None = None
    candidate_prefix: int | None = historical_prefix.prefixlen if historical_prefix else None
    if candidate is None:
        blockers.append(
            RecoveryBlocker(
                code="COLLISION_SAFE_ADDRESS_UNAVAILABLE",
                summary="No address is authoritatively reserved for temporary SwitchOps use.",
            )
        )
        missing.append(
            "A failed ping, absent neighbor entry, or historical ownership does not prove an address is unused."
        )
    else:
        try:
            address = ipaddress.ip_address(candidate.address)
            valid = bool(
                historical_prefix
                and isinstance(address, ipaddress.IPv4Address)
                and address in historical_prefix
                and str(address) != target
                and candidate.prefix_length == historical_prefix.prefixlen
                and candidate.assurance == "authoritative-reservation"
            )
        except ValueError:
            valid = False
        if valid:
            candidate_address = str(address)
            candidate_prefix = candidate.prefix_length
        else:
            blockers.append(
                RecoveryBlocker(
                    code="CANDIDATE_ADDRESS_INVALID",
                    summary="The candidate is not an authoritative, in-prefix reservation distinct from the target.",
                )
            )

    if meraki.state in {"not-configured", "unavailable"}:
        missing.append("Current Meraki LAN and port configuration is unavailable.")
    elif meraki.freshness in {"stale", "historical"}:
        missing.append("Meraki management evidence is stale and cannot change recovery readiness.")
    if not meraki.catalyst_port_identified:
        missing.append("The Catalyst-facing MX port has not been identified with current evidence.")

    operation = RecoveryOperation(
        kind="TEMPORARY_SECONDARY_IPV4",
        adapterId=current.adapter_id,
        candidateAddress=candidate_address,
        prefixLength=candidate_prefix,
        expectedRoute=str(historical_prefix) if historical_prefix else None,
    )
    return result(
        "BLOCKED" if blockers else "READY",
        "TEMPORARY_SECONDARY_IPV4",
        "Temporary management address candidate",
        (
            "A temporary secondary IPv4 address could recreate an on-link management path, "
            "but every blocker must be resolved and the bound state revalidated first."
        ),
        operation,
    )
