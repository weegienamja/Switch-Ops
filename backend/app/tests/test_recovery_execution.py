from __future__ import annotations

from datetime import datetime, timezone
import inspect

import pytest

from app import recovery_execution as execution_module
from app.recovery_execution import (
    RecoveryJournalRecord,
    RecoveryOwnershipRecord,
    RecoveryVerificationInput,
    assess_execution_gate,
    assess_recovery_restart,
    build_planning_architecture,
    evaluate_recovery_verification,
    select_owned_rollback,
)


NOW = datetime(2026, 8, 26, 12, 0, tzinfo=timezone.utc)


def successful_verification(**overrides) -> RecoveryVerificationInput:
    values = {
        "address_state": "PREFERRED",
        "on_link_route_present": True,
        "primary_address_unchanged": True,
        "default_route_unchanged": True,
        "dns_unchanged": True,
        "dhcp_preserved": True,
        "internet_preserved": True,
        "neighbor_plausible": True,
        "tcp22_reachable": True,
        "ssh": "CONNECTED",
        "read_only_observation_succeeded": True,
        "device_identity_matches": True,
        "management_path_healthy": True,
    }
    values.update(overrides)
    return RecoveryVerificationInput(**values)


def owner(**overrides) -> RecoveryOwnershipRecord:
    values = {
        "plan_id": "recovery-plan-synthetic",
        "operation_id": "recovery-operation-synthetic",
        "adapter_id": "adapter-synthetic",
        "interface_luid": 4242,
        "address": "203.0.113.250",
        "prefix_length": 24,
        "created_at": NOW,
        "previous_state_fingerprint": "before-synthetic",
        "system_object_key": "luid:4242|203.0.113.250/24",
        "post_apply_fingerprint": "owned-object-synthetic",
    }
    values.update(overrides)
    return RecoveryOwnershipRecord(**values)


def test_blocked_plan_and_ready_plan_both_fail_closed_without_executor() -> None:
    blocked = assess_execution_gate(
        plan_status="BLOCKED",
        binding_valid=True,
        blocker_codes=["COLLISION_SAFE_ADDRESS_UNAVAILABLE"],
    )
    ready = assess_execution_gate(plan_status="READY", binding_valid=True)

    assert blocked.allowed is ready.allowed is False
    assert blocked.disposition == "BLOCKED"
    assert "PLAN_NOT_READY" in blocked.reasons
    assert "PLAN_BLOCKER:COLLISION_SAFE_ADDRESS_UNAVAILABLE" in blocked.reasons
    assert ready.disposition == "NOT_IMPLEMENTED"
    assert ready.reasons == ["EXECUTOR_NOT_IMPLEMENTED"]


def test_stale_binding_and_incomplete_transaction_block_execution() -> None:
    result = assess_execution_gate(
        plan_status="READY",
        binding_valid=False,
        incomplete_transaction=True,
    )

    assert result.allowed is False
    assert result.reasons == [
        "PLAN_BINDING_CHANGED",
        "INCOMPLETE_TRANSACTION_REQUIRES_RECONCILIATION",
        "EXECUTOR_NOT_IMPLEMENTED",
    ]


def test_architecture_grants_no_shell_or_network_mutation_authority() -> None:
    architecture = build_planning_architecture(
        plan_status="READY", blocker_codes=[]
    )

    assert architecture.executor_implemented is False
    assert architecture.approval_available is False
    assert architecture.authority.current_policy == "MANUAL_ONLY"
    assert architecture.authority.future_policy_ceiling == "OPERATOR_APPROVED"
    assert architecture.authority.automatic_execution_enabled is False
    assert architecture.primitive.selected_primitive == "NONE"
    assert architecture.ownership.broad_cleanup_allowed is False
    assert architecture.transaction.journal_required_before_apply is True


@pytest.mark.parametrize(
    "state,timed_out,expected_outcome,expected_reason",
    [
        ("DUPLICATE", False, "ROLLBACK_REQUIRED", "ADDRESS_DUPLICATE"),
        ("INVALID", False, "ROLLBACK_REQUIRED", "ADDRESS_INVALID"),
        ("ABSENT", False, "ROLLBACK_REQUIRED", "ADDRESS_ABSENT"),
        ("TENTATIVE", False, "WAIT", None),
        ("TENTATIVE", True, "ROLLBACK_REQUIRED", "DAD_TIMEOUT"),
    ],
)
def test_address_readiness_and_dad_are_mandatory(
    state, timed_out, expected_outcome, expected_reason
) -> None:
    result = evaluate_recovery_verification(
        successful_verification(address_state=state, dad_timed_out=timed_out)
    )

    assert result.outcome == expected_outcome
    assert result.rollback_reasons == ([] if expected_reason is None else [expected_reason])


def test_preferred_address_can_complete_progressive_verification() -> None:
    result = evaluate_recovery_verification(successful_verification())

    assert result.outcome == "SUCCESS"
    assert result.rollback_reasons == []
    assert result.checks[0].code == "ADDRESS_PREFERRED"
    assert result.checks[-1].code == "MANAGEMENT_PATH_HEALTHY"
    assert all(check.status == "PASS" for check in result.checks)


@pytest.mark.parametrize(
    "field,reason",
    [
        ("on_link_route_present", "ON_LINK_ROUTE_PRESENT"),
        ("primary_address_unchanged", "PRIMARY_ADDRESS_UNCHANGED"),
        ("default_route_unchanged", "DEFAULT_ROUTE_UNCHANGED"),
        ("dns_unchanged", "DNS_UNCHANGED"),
        ("dhcp_preserved", "DHCP_PRESERVED"),
        ("internet_preserved", "INTERNET_PRESERVED"),
        ("neighbor_plausible", "NEIGHBOR_PLAUSIBLE"),
        ("tcp22_reachable", "TCP22_REACHABLE"),
    ],
)
def test_host_and_internet_invariant_failure_requires_rollback(field, reason) -> None:
    result = evaluate_recovery_verification(
        successful_verification(**{field: False})
    )

    assert result.outcome == "ROLLBACK_REQUIRED"
    assert result.rollback_reasons == [reason]


@pytest.mark.parametrize(
    "ssh,reason",
    [
        ("UNREACHABLE", "SSH_UNREACHABLE"),
        ("AUTHENTICATION_FAILED", "SSH_AUTHENTICATION_FAILED"),
        ("HOST_KEY_CHANGED", "SSH_HOST_KEY_CHANGED"),
    ],
)
def test_ssh_failures_after_path_restoration_require_rollback(ssh, reason) -> None:
    result = evaluate_recovery_verification(successful_verification(ssh=ssh))

    assert result.outcome == "ROLLBACK_REQUIRED"
    assert result.rollback_reasons == [reason]


def test_ssh_verification_cannot_be_skipped() -> None:
    result = evaluate_recovery_verification(
        successful_verification(ssh="NOT_ATTEMPTED")
    )

    assert result.outcome == "WAIT"
    assert result.checks[-1].code == "SSH_NOT_ATTEMPTED"


@pytest.mark.parametrize(
    "field,reason",
    [
        ("read_only_observation_succeeded", "READ_ONLY_OBSERVATION_SUCCEEDED"),
        ("device_identity_matches", "DEVICE_IDENTITY_MATCHES"),
        ("management_path_healthy", "MANAGEMENT_PATH_HEALTHY"),
    ],
)
def test_device_identity_and_final_assurance_fail_closed(field, reason) -> None:
    result = evaluate_recovery_verification(
        successful_verification(**{field: False})
    )

    assert result.outcome == "ROLLBACK_REQUIRED"
    assert result.rollback_reasons == [reason]


def test_rollback_selects_only_the_exact_owned_object() -> None:
    ownership = owner()
    observed = {
        "luid:4242|203.0.113.1/24": "preexisting-gateway",
        ownership.system_object_key: ownership.post_apply_fingerprint,
        "luid:4242|203.0.113.200/24": "preexisting-host",
    }

    decision = select_owned_rollback(ownership, observed)

    assert decision.disposition == "REMOVE_EXACT_OWNED_OBJECT"
    assert decision.system_object_key == ownership.system_object_key


def test_rollback_refuses_ambiguous_replacement_and_treats_absence_as_complete() -> None:
    ownership = owner()

    ambiguous = select_owned_rollback(
        ownership,
        {ownership.system_object_key: "different-object-fingerprint"},
    )
    absent = select_owned_rollback(ownership, {})

    assert ambiguous.disposition == "MANUAL_RECONCILIATION_REQUIRED"
    assert ambiguous.system_object_key is None
    assert absent.disposition == "ALREADY_ABSENT"


def test_restart_surfaces_incomplete_owned_state_and_blocks_new_recovery() -> None:
    record = RecoveryJournalRecord(
        transactionId="transaction-synthetic",
        state="VERIFYING",
        ownership=owner(),
    )

    result = assess_recovery_restart([record])

    assert result.disposition == "OPERATOR_RECONCILIATION_REQUIRED"
    assert result.incomplete_transaction_ids == ["transaction-synthetic"]
    assert result.new_recovery_allowed is False


def test_restart_accepts_only_terminal_or_empty_journals() -> None:
    completed = RecoveryJournalRecord(
        transactionId="transaction-complete",
        state="ROLLED_BACK",
        ownership=owner(),
    )

    assert assess_recovery_restart([]).new_recovery_allowed is True
    assert assess_recovery_restart([completed]).disposition == "CLEAR"


def test_safety_module_has_no_process_or_network_mutation_runtime() -> None:
    source = inspect.getsource(execution_module)
    for forbidden in (
        "subprocess",
        "powershell",
        "cmd.exe",
        "netsh",
        "New-NetIPAddress",
        "Set-NetIPInterface",
        "CreateUnicastIpAddressEntry",
        "DeleteUnicastIpAddressEntry",
    ):
        assert forbidden not in source


def test_the_primitive_rationale_reflects_the_measured_dhcp_result() -> None:
    """The published rationale must not still call DHCP preservation unproven.

    It was unproven when this contract was written. Gate 2 measured it on a
    disposable DHCP adapter, so leaving the old wording in place would
    understate the evidence to anyone reading the architecture endpoint.
    """
    rationale = " ".join(build_planning_architecture(plan_status="BLOCKED", blocker_codes=()).primitive.rationale)
    assert "preservation is not yet proven" not in rationale
    assert "disposable DHCP adapter" in rationale
    # ...but it must not be inflated into a production claim either.
    assert "unproven across the production adapter" in rationale


def test_the_primitive_is_still_not_selected_after_gate_two() -> None:
    primitive = build_planning_architecture(plan_status="BLOCKED", blocker_codes=()).primitive
    assert primitive.selected_primitive == "NONE"
    # Gate 3 is an isolated experiment that has not run, so isolated
    # validation is still what the candidate is waiting on.
    assert primitive.candidate_status == "ISOLATED_VALIDATION_REQUIRED"
