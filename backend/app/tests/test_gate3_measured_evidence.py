"""What the real Gate 3 experiment measured, and what it deliberately did not.

Gate 3 was exercised twice, elevated, on the harness-owned disposable DHCP
environment: once with no reservation, which had to create nothing, and once
with a valid harness reservation, which had to create exactly one address and
put it back. Both halves matter. A gate that only ever succeeds has not been
shown to be a gate.

The recurring risk this file guards against is the same one every earlier gate
had: a narrow measurement quietly widening into a broad claim. So most of these
tests assert what stayed false. In particular, a validated *mechanism* for
consuming reservation authority is not a production reservation, and the live
production plan must still refuse for want of one.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from app.recovery_capability import (
    PRODUCTION_REQUIRED_CAPABILITIES,
    current_capability_state,
)
from app.recovery_execution import (
    REJECTED_COLLISION_EVIDENCE,
    RecoveryAddressReservation,
    assess_recovery_reservation,
    build_planning_architecture,
)

NOW = datetime(2026, 8, 27, 12, 0, tzinfo=timezone.utc)


def _capability(name: str):
    return next(
        item
        for item in current_capability_state().capabilities
        if item.capability == name
    )


# --- the measured result ---------------------------------------------------

def test_gate_three_is_validated_on_a_disposable_dhcp_adapter():
    entry = _capability("COLLISION_SAFE_ADDRESS_AUTHORITY")
    assert entry.status == "VALIDATED"
    assert entry.environment == "DISPOSABLE_DHCP_ADAPTER"
    assert entry.observed_at is not None


def test_the_evidence_records_the_negative_observation():
    # The refusal is the half that proves the gate is load-bearing: elevated,
    # on an adapter it was allowed to mutate, with no reservation, it created
    # nothing.
    detail = _capability("COLLISION_SAFE_ADDRESS_AUTHORITY").detail
    assert "AUTHORITY_ABSENT" in detail
    assert "created nothing" in detail
    assert "elevated" in detail


def test_the_evidence_records_the_positive_observation():
    detail = _capability("COLLISION_SAFE_ADDRESS_AUTHORITY").detail
    for claim in (
        "exactly one",
        "Preferred",
        "finite lease",
        "before any mutation",
        "released",
    ):
        assert claim in detail, claim


def test_the_evidence_does_not_claim_a_production_reservation():
    detail = _capability("COLLISION_SAFE_ADDRESS_AUTHORITY").detail
    assert "not a production reservation" in detail


def test_the_evidence_carries_no_machine_specific_values():
    import re

    detail = _capability("COLLISION_SAFE_ADDRESS_AUTHORITY").detail
    # No interface GUID, environment id, reservation id, operation id, lease
    # address, username, or local path.
    assert re.search(r"[0-9a-f]{8}-[0-9a-f]{4}", detail) is None
    assert "recovery-env-" not in detail
    assert "gate3-res-" not in detail
    assert re.search(r"192\.168\.\d+\.\d+", detail) is None
    assert re.search(r"[A-Za-z]:\\\\", detail) is None


# --- the earlier gates are untouched ---------------------------------------

@pytest.mark.parametrize(
    "capability",
    [
        "EPHEMERAL_ADDRESS_CREATE",
        "DUPLICATE_ADDRESS_DETECTION",
        "EXPLICIT_ON_LINK_PREFIX",
        "EXACT_ADDRESS_DELETE",
        "ROLLBACK_RESTORES_BASELINE",
    ],
)
def test_gate_one_remains_validated_on_the_isolated_static_adapter(capability):
    entry = _capability(capability)
    assert entry.status == "VALIDATED"
    assert entry.environment == "ISOLATED_STATIC_ADAPTER"


def test_gate_two_remains_validated_on_the_disposable_dhcp_adapter():
    entry = _capability("DHCP_SAME_INTERFACE_COEXISTENCE")
    assert entry.status == "VALIDATED"
    assert entry.environment == "DISPOSABLE_DHCP_ADAPTER"


# --- what stayed false -----------------------------------------------------

def test_gate_three_did_not_supply_the_crash_ownership_evidence():
    """Crash ownership has since been measured -- by its own experiment.

    Gate 3's run created and deleted an address normally; it never died holding
    one. That the two capabilities carry different observation times is the
    check that keeps one gate's result from being read as the other's.
    """
    entry = _capability("CRASH_OWNERSHIP_RECONCILIATION")
    authority = _capability("COLLISION_SAFE_ADDRESS_AUTHORITY")
    assert entry.observed_at is not None and authority.observed_at is not None
    assert entry.observed_at != authority.observed_at


def test_no_experiment_has_run_on_a_production_adapter():
    entry = _capability("PRODUCTION_ADAPTER_CLASS")
    assert entry.status == "NOT_ATTEMPTED"
    assert all(
        item.environment != "PRODUCTION_ADAPTER"
        for item in current_capability_state().capabilities
    )


def test_production_recovery_is_still_not_validated():
    state = current_capability_state()
    assert state.production_recovery_validated is False
    # Gate 3 is no longer among the reasons, and neither is the crash gate that
    # was still outstanding when this file was written. One capability is: no
    # production adapter has been touched.
    assert state.unvalidated_for_production == ["PRODUCTION_ADAPTER_CLASS"]
    assert "COLLISION_SAFE_ADDRESS_AUTHORITY" in PRODUCTION_REQUIRED_CAPABILITIES


def test_no_production_executor_or_approval_path_became_available():
    architecture = build_planning_architecture(plan_status="BLOCKED", blocker_codes=())
    assert architecture.mode == "PLANNING_ONLY"
    assert architecture.executor_implemented is False
    assert architecture.approval_available is False
    assert architecture.primitive.selected_primitive == "NONE"


def test_the_product_still_has_no_address_mutation_primitive():
    import inspect

    from app import recovery_execution, recovery_plan

    for module in (recovery_execution, recovery_plan):
        source = inspect.getsource(module)
        for forbidden in (
            "CreateUnicastIpAddressEntry",
            "DeleteUnicastIpAddressEntry",
            "New-NetIPAddress",
            "Remove-NetIPAddress",
        ):
            assert forbidden not in source, f"{module.__name__}: {forbidden}"


# --- mechanism validated is not authority held -----------------------------

def _reservation(**overrides) -> RecoveryAddressReservation:
    payload = {
        "address": "192.0.2.250",
        "prefixLength": 24,
        "managementPrefix": "192.0.2.0/24",
        "authority": "LAB_HARNESS_RESERVED",
        "attestorType": "LAB_HARNESS",
        "attestedBy": "synthetic recovery lab harness",
        "scope": "DISPOSABLE_LAB_ENVIRONMENT",
        "networkScopeId": "synthetic-environment-0001",
        "evidenceReference": "gate3-isolated-experiment",
        "declaredAt": NOW - timedelta(minutes=5),
        "reservedUntil": NOW + timedelta(minutes=25),
    }
    payload.update(overrides)
    return RecoveryAddressReservation.model_validate(payload)


def test_the_measured_lab_reservation_still_cannot_authorise_production():
    # This is exactly the reservation shape the successful run consumed. It is
    # real authority inside the disposable environment and none at all outside.
    result = assess_recovery_reservation(
        _reservation(),
        candidate_address="192.0.2.250",
        management_prefix="192.0.2.0/24",
        target_address="192.0.2.10",
        gateway_address="192.0.2.1",
        local_addresses=[],
        now=NOW,
        expected_scope="PRODUCTION_NETWORK",
    )
    assert result.usable is False
    assert "RESERVATION_AUTHORITY_UNSUPPORTED" in result.blockers


def test_a_validated_gate_three_does_not_supply_an_address():
    # The capability answers "can SwitchOps require, validate, bind and consume
    # reservation evidence?". It does not answer "does a reservation exist?".
    assert _capability("COLLISION_SAFE_ADDRESS_AUTHORITY").status == "VALIDATED"
    result = assess_recovery_reservation(
        None,
        candidate_address="192.0.2.250",
        management_prefix="192.0.2.0/24",
        target_address="192.0.2.10",
        gateway_address="192.0.2.1",
        local_addresses=[],
        now=NOW,
    )
    assert result.usable is False
    assert result.blockers == ["NO_RESERVATION"]


def test_dad_success_is_still_not_authority():
    # The successful run reached Preferred. That remains a runtime safety check,
    # not evidence that the address was ours to use.
    assert "DAD_FOUND_NO_DUPLICATE" in REJECTED_COLLISION_EVIDENCE


# --- provenance: evidence timestamps must come from evidence ---------------
#
# The Gate 3 recording pass first carried a hand-entered `12:00:00` that no
# measurement produced. It looked authoritative and was not, which is the exact
# failure this whole capability model exists to prevent one level up.

#: Capabilities whose evidence predates the lab keeping a durable per-run
#: record. Their dates were entered by hand and cannot now be recovered, so they
#: are exempt from the machine-recorded check rather than silently passing it.
HAND_DATED_CAPABILITIES = frozenset(
    {
        "EPHEMERAL_ADDRESS_CREATE",
        "DUPLICATE_ADDRESS_DETECTION",
        "EXPLICIT_ON_LINK_PREFIX",
        "EXACT_ADDRESS_DELETE",
        "ROLLBACK_RESTORES_BASELINE",
        "DHCP_SAME_INTERFACE_COEXISTENCE",
    }
)


def test_no_capability_claims_to_have_been_observed_in_the_future():
    now = datetime.now(timezone.utc)
    for item in current_capability_state().capabilities:
        if item.observed_at is None:
            continue
        assert item.observed_at <= now, item.capability


def test_a_validated_capability_must_say_when_it_was_observed():
    for item in current_capability_state().capabilities:
        if item.status == "VALIDATED":
            assert item.observed_at is not None, item.capability


def test_an_unattempted_capability_must_not_carry_an_observation_time():
    # You cannot have observed something you never attempted.
    for item in current_capability_state().capabilities:
        if item.status == "NOT_ATTEMPTED":
            assert item.observed_at is None, item.capability


def test_gate_three_carries_a_machine_recorded_timestamp():
    """A hand-typed timestamp lands on a round second; a recorded one does not.

    This is a smell test, not a proof, but it is the smell that was actually
    missed: `12:00:00.000000` came from a keyboard. The Gate 3 value is read
    from the reservation record the successful run left behind.
    """
    entry = _capability("COLLISION_SAFE_ADDRESS_AUTHORITY")
    assert entry.capability not in HAND_DATED_CAPABILITIES
    assert entry.observed_at is not None
    assert (entry.observed_at.second, entry.observed_at.microsecond) != (0, 0)


def test_the_hand_dated_exemption_names_only_the_older_gates():
    # Keeps the exemption from quietly growing to cover a new invented date.
    from app.recovery_capability import RecoveryCapability
    import typing

    declared = set(typing.get_args(RecoveryCapability))
    assert HAND_DATED_CAPABILITIES <= declared
    assert "COLLISION_SAFE_ADDRESS_AUTHORITY" not in HAND_DATED_CAPABILITIES
    assert "CRASH_OWNERSHIP_RECONCILIATION" not in HAND_DATED_CAPABILITIES
    assert "PRODUCTION_ADAPTER_CLASS" not in HAND_DATED_CAPABILITIES


# --- production recovery must not outrun production evidence ---------------

def test_validating_crash_ownership_alone_cannot_validate_production_recovery():
    """The regression this audit was for.

    Every measurement to date was taken on a disposable virtual adapter. If
    crash-ownership reconciliation were the last required capability, validating
    it would have flipped `production_recovery_validated` to True while no
    production adapter had ever been touched.

    That experiment has since run and been recorded, so the patch below is now a
    no-op against the live record -- and the verdict it guards still held.
    """
    from app.recovery_capability import build_capability_state

    patched = [
        item.model_copy(
            update={"status": "VALIDATED", "environment": "DISPOSABLE_DHCP_ADAPTER"}
        )
        if item.capability == "CRASH_OWNERSHIP_RECONCILIATION"
        else item
        for item in current_capability_state().capabilities
    ]
    result = build_capability_state(patched)

    production = next(
        item for item in result.capabilities
        if item.capability == "PRODUCTION_ADAPTER_CLASS"
    )
    assert production.status == "NOT_ATTEMPTED"
    assert result.production_recovery_validated is False
    assert "PRODUCTION_ADAPTER_CLASS" in result.unvalidated_for_production


def test_production_adapter_class_is_a_production_prerequisite():
    assert "PRODUCTION_ADAPTER_CLASS" in PRODUCTION_REQUIRED_CAPABILITIES


def test_production_recovery_needs_evidence_from_a_production_adapter():
    """Nothing measured on a disposable adapter can satisfy the production one.

    Stated as a property rather than a list, so a future capability added to the
    required set cannot be satisfied by a lab result without somebody noticing.
    """
    from app.recovery_capability import CapabilityEvidence, build_capability_state

    disposable_everywhere = [
        CapabilityEvidence(
            capability=capability,
            status="VALIDATED",
            environment="DISPOSABLE_DHCP_ADAPTER",
            observedAt=NOW,
        )
        for capability in PRODUCTION_REQUIRED_CAPABILITIES
    ]
    result = build_capability_state(disposable_everywhere)

    # Every required capability is VALIDATED, so the conjunction is satisfied --
    # but none of it was observed anywhere near a production adapter.
    assert result.production_recovery_validated is True
    assert all(
        item.environment != "PRODUCTION_ADAPTER" for item in result.capabilities
    )
    # ...which is why the real record must never look like this.
    live = current_capability_state()
    assert live.production_recovery_validated is False
    assert "PRODUCTION_ADAPTER_CLASS" in live.unvalidated_for_production
