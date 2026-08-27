"""Recovery addressing and execution readiness are planning judgements.

Nothing here executes anything. The tests exist to pin two refusals that matter:
SwitchOps must never choose a recovery address by probing, and it must never ask
an operator to approve an operation whose prerequisites are unproven.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from backend.app.recovery_execution import (
    REJECTED_COLLISION_EVIDENCE,
    ExecutionReadinessInput,
    RecoveryAddressReservation,
    assess_recovery_reservation,
    evaluate_execution_readiness,
)

NOW = datetime(2026, 8, 27, 12, 0, 0, tzinfo=timezone.utc)
PREFIX = "192.0.2.0/24"
TARGET = "192.0.2.10"
GATEWAY = "192.0.2.1"


#: Each authority type names exactly one kind of attestor, so a fixture that
#: changes one without the other is testing a masquerade rather than a variant.
ATTESTOR_FOR = {
    "OPERATOR_DECLARED": "NAMED_OPERATOR",
    "DHCP_EXCLUSION_ATTESTED": "DHCP_SERVICE_RECORD",
    "INFRASTRUCTURE_ATTESTED": "IPAM_RECORD",
    "LAB_HARNESS_RESERVED": "LAB_HARNESS",
}


def _reservation(address: str = "192.0.2.250", **overrides) -> RecoveryAddressReservation:
    authority = overrides.get("authority", "OPERATOR_DECLARED")
    payload = {
        "address": address,
        "prefixLength": 24,
        "managementPrefix": PREFIX,
        "authority": authority,
        "attestorType": ATTESTOR_FOR[authority],
        "declaredAt": NOW - timedelta(days=1),
        "reservedUntil": NOW + timedelta(days=1),
        "attestedBy": "lab operator",
        "scope": "PRODUCTION_NETWORK",
        "networkScopeId": "management-vlan-test",
        "evidenceReference": "change-ticket-test-0001",
    }
    payload.update(overrides)
    return RecoveryAddressReservation.model_validate(payload)


def _assess(reservation, **overrides):
    kwargs = {
        "candidate_address": (
            reservation.address if reservation is not None else "192.0.2.250"
        ),
        "management_prefix": PREFIX,
        "target_address": TARGET,
        "gateway_address": GATEWAY,
        "local_addresses": [],
        "now": NOW,
    }
    kwargs.update(overrides)
    return assess_recovery_reservation(reservation, **kwargs)


# --- reservation -----------------------------------------------------------

def test_no_reservation_is_blocked_rather_than_guessed():
    result = _assess(None)
    assert result.usable is False
    assert result.blockers == ["NO_RESERVATION"]
    assert "probing" in " ".join(result.evidence)


def test_an_explicit_reservation_inside_the_prefix_is_usable():
    result = _assess(_reservation())
    assert result.usable is True
    assert result.blockers == []


@pytest.mark.parametrize(
    "authority",
    ["OPERATOR_DECLARED", "DHCP_EXCLUSION_ATTESTED", "INFRASTRUCTURE_ATTESTED"],
)
def test_every_accepted_authority_can_authorise_an_address(authority):
    assert _assess(_reservation(authority=authority)).usable is True


def test_a_reservation_outside_the_management_prefix_is_blocked():
    # An address off-prefix would not create an on-link path to the target,
    # so the recovery would appear to succeed and reach nothing.
    result = _assess(_reservation("198.51.100.250"))
    assert result.usable is False
    assert "RESERVATION_OUTSIDE_MANAGEMENT_PREFIX" in result.blockers


def test_the_target_address_cannot_be_reserved_for_recovery():
    result = _assess(_reservation(TARGET))
    assert result.usable is False
    assert "RESERVATION_IS_TARGET_ADDRESS" in result.blockers


def test_the_gateway_address_cannot_be_reserved_for_recovery():
    result = _assess(_reservation(GATEWAY))
    assert result.usable is False
    assert "RESERVATION_IS_GATEWAY_ADDRESS" in result.blockers


@pytest.mark.parametrize("address", ["192.0.2.0", "192.0.2.255"])
def test_network_and_broadcast_addresses_are_blocked(address):
    result = _assess(_reservation(address))
    assert result.usable is False
    assert "RESERVATION_IS_NETWORK_OR_BROADCAST" in result.blockers


def test_a_reservation_this_host_already_holds_is_blocked():
    # If we already hold it, a created address could not afterwards be
    # distinguished as ours, so rollback could remove somebody else's.
    result = _assess(_reservation(), local_addresses=["192.0.2.250"])
    assert result.usable is False
    assert "RESERVATION_CONFLICTS_WITH_LOCAL_ADDRESS" in result.blockers


def test_a_stale_attestation_is_blocked_until_re_attested():
    old = _reservation(declaredAt=NOW - timedelta(days=800))
    result = _assess(old)
    assert result.usable is False
    assert "RESERVATION_EVIDENCE_STALE" in result.blockers


def test_probe_based_evidence_is_named_as_rejected():
    # These are the tempting-but-wrong signals. Keeping them enumerated stops
    # a future change quietly promoting one to acceptable evidence.
    assert "ICMP_SILENCE" in REJECTED_COLLISION_EVIDENCE
    assert "STALE_ARP_ABSENCE" in REJECTED_COLLISION_EVIDENCE


# --- execution readiness ---------------------------------------------------

def _all_satisfied(**overrides) -> ExecutionReadinessInput:
    payload = {
        "primitiveValidated": True,
        "dhcpCoexistenceValidated": True,
        "elevationAvailable": True,
        "managementPrefixKnown": True,
        "targetIdentityTrusted": True,
        "hostAdapterIdentified": True,
        "hostAdapterUp": True,
        "dhcpStateEstablished": True,
        "reservationUsable": True,
        "planBindingCurrent": True,
        "defaultRouteBaselineCaptured": True,
        "dnsBaselineCaptured": True,
        "rollbackVerified": True,
        "journalAvailable": True,
    }
    payload.update(overrides)
    return ExecutionReadinessInput.model_validate(payload)


def test_readiness_defaults_to_unproven_on_every_axis():
    decision = evaluate_execution_readiness(ExecutionReadinessInput())
    assert decision.readiness == "NOT_SUPPORTED"
    assert decision.may_request_operator_approval is False
    assert decision.satisfied == []


def test_full_evidence_permits_asking_but_not_acting():
    decision = evaluate_execution_readiness(_all_satisfied())
    assert decision.readiness == "READY"
    # READY authorises a question, never an action.
    assert decision.may_request_operator_approval is True
    assert decision.unmet == []
    assert "may not act" in decision.summary


def test_an_unvalidated_primitive_is_not_supported_not_merely_blocked():
    # More network evidence cannot fix an unvalidated primitive, so the
    # distinction matters: NOT_SUPPORTED tells the operator to go validate.
    decision = evaluate_execution_readiness(_all_satisfied(primitiveValidated=False))
    assert decision.readiness == "NOT_SUPPORTED"
    assert decision.may_request_operator_approval is False


@pytest.mark.parametrize(
    "field,code",
    [
        ("elevationAvailable", "ELEVATION_AVAILABLE"),
        ("planBindingCurrent", "PLAN_BINDING_CURRENT"),
        ("reservationUsable", "RESERVATION_USABLE"),
        ("hostAdapterIdentified", "HOST_ADAPTER_IDENTIFIED"),
        ("hostAdapterUp", "HOST_ADAPTER_UP"),
        ("dhcpStateEstablished", "DHCP_STATE_ESTABLISHED"),
        ("targetIdentityTrusted", "TARGET_IDENTITY_TRUSTED"),
        ("defaultRouteBaselineCaptured", "DEFAULT_ROUTE_BASELINE_CAPTURED"),
        ("dnsBaselineCaptured", "DNS_BASELINE_CAPTURED"),
        ("rollbackVerified", "ROLLBACK_VERIFIED"),
        ("journalAvailable", "JOURNAL_AVAILABLE"),
        ("managementPrefixKnown", "MANAGEMENT_PREFIX_KNOWN"),
    ],
)
def test_any_single_missing_prerequisite_blocks_approval(field, code):
    decision = evaluate_execution_readiness(_all_satisfied(**{field: False}))
    assert decision.readiness == "BLOCKED"
    assert decision.may_request_operator_approval is False
    assert code in decision.unmet


def test_blocked_decisions_still_report_what_is_proven():
    decision = evaluate_execution_readiness(_all_satisfied(rollbackVerified=False))
    assert "ELEVATION_AVAILABLE" in decision.satisfied
    assert decision.unmet == ["ROLLBACK_VERIFIED"]


# --- READY under the split capability model --------------------------------

def test_the_proven_primitive_alone_is_not_supported():
    # GATE 1 proven, GATE 2 not. This is exactly today's real state.
    decision = evaluate_execution_readiness(
        ExecutionReadinessInput(primitiveValidated=True)
    )
    assert decision.readiness == "NOT_SUPPORTED"
    assert decision.may_request_operator_approval is False
    assert "DHCP_COEXISTENCE_VALIDATED" in decision.unmet
    assert "DHCP-controlled" in decision.summary


def test_dhcp_validation_without_the_primitive_is_still_not_supported():
    decision = evaluate_execution_readiness(
        ExecutionReadinessInput(dhcpCoexistenceValidated=True)
    )
    assert decision.readiness == "NOT_SUPPORTED"


def test_both_capabilities_plus_no_reservation_is_blocked_not_ready():
    # Capability gaps are closed by experiments; this one is closed by the
    # operator reserving an address, so it is BLOCKED rather than NOT_SUPPORTED.
    decision = evaluate_execution_readiness(
        _all_satisfied(reservationUsable=False)
    )
    assert decision.readiness == "BLOCKED"
    assert "RESERVATION_USABLE" in decision.unmet


def test_a_reservation_without_dhcp_validation_is_not_supported():
    decision = evaluate_execution_readiness(
        _all_satisfied(dhcpCoexistenceValidated=False)
    )
    assert decision.readiness == "NOT_SUPPORTED"
    assert decision.may_request_operator_approval is False


def test_capability_gaps_are_not_supported_and_evidence_gaps_are_blocked():
    # The distinction matters: one tells the operator to run an experiment,
    # the other tells them to gather evidence.
    capability_gap = evaluate_execution_readiness(
        _all_satisfied(dhcpCoexistenceValidated=False)
    )
    evidence_gap = evaluate_execution_readiness(_all_satisfied(planBindingCurrent=False))
    assert capability_gap.readiness == "NOT_SUPPORTED"
    assert evidence_gap.readiness == "BLOCKED"


def test_full_evidence_including_both_capabilities_is_ready():
    decision = evaluate_execution_readiness(_all_satisfied())
    assert decision.readiness == "READY"
    assert decision.may_request_operator_approval is True
    assert "DHCP_COEXISTENCE_VALIDATED" in decision.satisfied


# --- what Gate 2 passing must NOT unlock -----------------------------------

def test_gate_two_alone_does_not_reach_ready():
    # Real evidence now exists for the primitive and for DHCP coexistence.
    # Everything else about a specific network is still unproven.
    from backend.app.recovery_capability import dhcp_coexistence_validated

    assert dhcp_coexistence_validated() is True
    decision = evaluate_execution_readiness(
        ExecutionReadinessInput(primitiveValidated=True, dhcpCoexistenceValidated=True)
    )
    assert decision.readiness == "BLOCKED"
    assert decision.may_request_operator_approval is False
    assert "RESERVATION_USABLE" in decision.unmet


def test_gate_two_grants_no_reservation_authority():
    # A validated primitive says nothing about whether an address is free.
    decision = evaluate_execution_readiness(
        _all_satisfied(reservationUsable=False)
    )
    assert decision.readiness == "BLOCKED"
    assert "RESERVATION_USABLE" in decision.unmet
    assert decision.may_request_operator_approval is False


def test_no_reservation_still_blocks_after_gate_two():
    result = _assess(None)
    assert result.usable is False
    assert result.blockers == ["NO_RESERVATION"]


def test_weak_collision_evidence_is_still_rejected_after_gate_two():
    for weak in ("ICMP_SILENCE", "STALE_ARP_ABSENCE",
                 "ABSENT_FROM_LOCAL_NEIGHBOR_CACHE",
                 "UNUSED_IN_LAST_OBSERVED_MAC_TABLE"):
        assert weak in REJECTED_COLLISION_EVIDENCE


def test_the_product_remains_planning_only_after_gate_two():
    from backend.app.recovery_execution import RecoveryExecutionArchitecture

    fields = RecoveryExecutionArchitecture.model_fields
    assert fields["mode"].default == "PLANNING_ONLY"
    assert fields["executor_implemented"].default is False
    assert fields["approval_available"].default is False


def test_the_execution_gate_still_refuses_after_gate_two():
    from backend.app.recovery_execution import assess_execution_gate

    # Even a READY plan with a valid binding and no blockers is refused,
    # because there is still no executor to run it.
    decision = assess_execution_gate(
        plan_status="READY", binding_valid=True, blocker_codes=()
    )
    assert decision.allowed is False
    assert decision.disposition in ("BLOCKED", "NOT_IMPLEMENTED")


# --- Gate 3: implemented is not measured -----------------------------------

def test_gate_three_is_recorded_as_measured():
    from app.recovery_capability import current_capability_state

    entry = next(
        item
        for item in current_capability_state().capabilities
        if item.capability == "COLLISION_SAFE_ADDRESS_AUTHORITY"
    )
    assert entry.status == "VALIDATED"
    assert entry.environment == "DISPOSABLE_DHCP_ADAPTER"


def test_gate_three_is_still_required_for_production_recovery():
    from app.recovery_capability import (
        PRODUCTION_REQUIRED_CAPABILITIES,
        current_capability_state,
    )

    # Still a prerequisite, now a satisfied one. Production recovery stays
    # unvalidated on a different capability, not on this one.
    assert "COLLISION_SAFE_ADDRESS_AUTHORITY" in PRODUCTION_REQUIRED_CAPABILITIES
    state = current_capability_state()
    assert state.production_recovery_validated is False
    assert "COLLISION_SAFE_ADDRESS_AUTHORITY" not in state.unvalidated_for_production
    assert "CRASH_OWNERSHIP_RECONCILIATION" in state.unvalidated_for_production


def test_a_valid_reservation_does_not_make_the_product_an_executor():
    from app.recovery_execution import build_planning_architecture

    assert _assess(_reservation()).usable is True
    architecture = build_planning_architecture(plan_status="BLOCKED", blocker_codes=())
    assert architecture.executor_implemented is False
    assert architecture.approval_available is False
    assert architecture.mode == "PLANNING_ONLY"


def test_a_valid_reservation_alone_does_not_reach_ready():
    # Reservation authority closes one prerequisite. Readiness is the
    # conjunction of all of them, and a capability gap is not closed by evidence.
    decision = evaluate_execution_readiness(
        ExecutionReadinessInput(
            primitiveValidated=True,
            dhcpCoexistenceValidated=True,
            reservationUsable=True,
        )
    )
    assert decision.readiness != "READY"
    assert decision.may_request_operator_approval is False


def test_reservation_authority_does_not_grant_row_delete_ownership():
    # Which row is ours to delete is answered by the journal and the exact
    # LUID/index/address/prefix, never by who reserved the address.
    import inspect

    from app.recovery_execution import select_owned_rollback

    signature = inspect.signature(select_owned_rollback)
    assert "reservation" not in signature.parameters
