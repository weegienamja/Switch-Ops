"""Proving the primitive works must never imply production recovery is safe.

The isolated experiment succeeded on a statically addressed adapter. The whole
point of this model is that the result cannot leak sideways into a claim about
DHCP-controlled interfaces, so most of these tests are about what stays false.
"""
from __future__ import annotations

from datetime import datetime, timezone

import pytest

from backend.app.recovery_capability import (
    PRODUCTION_REQUIRED_CAPABILITIES,
    dhcp_coexistence_validated,
    CapabilityEvidence,
    build_capability_state,
    current_capability_state,
)

OBSERVED = datetime(2026, 8, 27, tzinfo=timezone.utc)


def _evidence(capability: str, status: str, environment: str = "ISOLATED_STATIC_ADAPTER"):
    return CapabilityEvidence(
        capability=capability,
        status=status,
        environment=environment,
        observedAt=OBSERVED,
    )


def _all_validated(**overrides):
    statuses = {capability: "VALIDATED" for capability in PRODUCTION_REQUIRED_CAPABILITIES}
    statuses.update(overrides)
    return [_evidence(capability, status) for capability, status in statuses.items()]


# --- the recorded state ----------------------------------------------------

def test_the_isolated_experiment_is_recorded_as_validated():
    state = current_capability_state()
    by_capability = {item.capability: item for item in state.capabilities}
    for capability in (
        "EPHEMERAL_ADDRESS_CREATE",
        "DUPLICATE_ADDRESS_DETECTION",
        "EXPLICIT_ON_LINK_PREFIX",
        "EXACT_ADDRESS_DELETE",
        "ROLLBACK_RESTORES_BASELINE",
    ):
        assert by_capability[capability].status == "VALIDATED", capability
        # Where it was observed is part of the evidence, not a footnote.
        assert by_capability[capability].environment == "ISOLATED_STATIC_ADAPTER"


def test_the_primitive_is_validated_but_production_recovery_is_not():
    state = current_capability_state()
    assert state.primitive_validated is True
    # Gate 2 passing does not make production recovery validated: crash
    # ownership reconciliation has still never been exercised.
    assert state.production_recovery_validated is False
    assert "CRASH_OWNERSHIP_RECONCILIATION" in state.unvalidated_for_production


def test_dhcp_coexistence_is_now_validated_on_a_disposable_dhcp_adapter():
    state = current_capability_state()
    entry = next(
        item
        for item in state.capabilities
        if item.capability == "DHCP_SAME_INTERFACE_COEXISTENCE"
    )
    assert entry.status == "VALIDATED"
    # Measured on a disposable adapter, never a production one.
    assert entry.environment == "DISPOSABLE_DHCP_ADAPTER"
    assert entry.observed_at is not None
    assert dhcp_coexistence_validated() is True


def test_the_coexistence_evidence_states_what_was_actually_measured():
    entry = next(
        item
        for item in current_capability_state().capabilities
        if item.capability == "DHCP_SAME_INTERFACE_COEXISTENCE"
    )
    detail = entry.detail
    for claim in ("DHCP/DHCP", "finite lease", "Preferred", "/24", "DNS"):
        assert claim in detail, claim


def test_the_coexistence_evidence_carries_no_machine_specific_values():
    # The measurement ran on a real adapter; the record must not carry its
    # GUID, environment id, probe VM id, or live lease address.
    import re

    detail = next(
        item.detail
        for item in current_capability_state().capabilities
        if item.capability == "DHCP_SAME_INTERFACE_COEXISTENCE"
    )
    assert re.search(r"[0-9a-f]{8}-[0-9a-f]{4}", detail) is None
    assert "recovery-env-" not in detail
    assert re.search(r"192\.168\.\d+\.\d+", detail) is None


def test_gate_two_does_not_validate_a_production_adapter():
    state = current_capability_state()
    assert all(
        item.environment != "PRODUCTION_ADAPTER" for item in state.capabilities
    )
    production = next(
        item
        for item in state.capabilities
        if item.capability == "PRODUCTION_ADAPTER_CLASS"
    )
    assert production.status == "NOT_ATTEMPTED"


def test_no_experiment_has_run_on_a_production_adapter():
    state = current_capability_state()
    entry = next(
        item for item in state.capabilities if item.capability == "PRODUCTION_ADAPTER_CLASS"
    )
    assert entry.status == "NOT_ATTEMPTED"
    assert all(
        item.environment != "PRODUCTION_ADAPTER" for item in state.capabilities
    )


def test_platform_is_recorded_as_a_class_not_a_build_number():
    # Pinning "build 26200" would make every Windows update look like a
    # regression, which the evidence does not support.
    state = current_capability_state()
    assert state.platform_class == "WINDOWS_10_OR_LATER_X64"
    assert "26200" not in state.model_dump_json()


# --- derivation ------------------------------------------------------------

def test_production_readiness_requires_every_capability():
    state = build_capability_state(_all_validated())
    assert state.production_recovery_validated is True
    assert state.unvalidated_for_production == []


@pytest.mark.parametrize("missing", PRODUCTION_REQUIRED_CAPABILITIES)
def test_any_single_unvalidated_capability_blocks_production(missing):
    state = build_capability_state(_all_validated(**{missing: "UNVALIDATED"}))
    assert state.production_recovery_validated is False
    assert missing in state.unvalidated_for_production


def test_a_failed_capability_is_not_treated_as_validated():
    state = build_capability_state(
        _all_validated(DHCP_SAME_INTERFACE_COEXISTENCE="FAILED")
    )
    assert state.production_recovery_validated is False
    assert "DHCP_SAME_INTERFACE_COEXISTENCE" in state.unvalidated_for_production


def test_a_capability_with_no_evidence_at_all_counts_as_unvalidated():
    # Silence is not validation.
    partial = [_evidence("EPHEMERAL_ADDRESS_CREATE", "VALIDATED")]
    state = build_capability_state(partial)
    assert state.production_recovery_validated is False
    assert len(state.unvalidated_for_production) == len(PRODUCTION_REQUIRED_CAPABILITIES) - 1


def test_dhcp_validation_can_be_recorded_separately_once_measured():
    # The path this stage is preparing: same primitive, new environment.
    state = build_capability_state(
        _all_validated()
        + [
            CapabilityEvidence(
                capability="DHCP_SAME_INTERFACE_COEXISTENCE",
                status="VALIDATED",
                environment="DISPOSABLE_DHCP_ADAPTER",
                observedAt=OBSERVED,
            )
        ]
    )
    assert state.production_recovery_validated is True


def test_primitive_validation_alone_does_not_set_production_validated():
    core_only = [
        _evidence("EPHEMERAL_ADDRESS_CREATE", "VALIDATED"),
        _evidence("DUPLICATE_ADDRESS_DETECTION", "VALIDATED"),
        _evidence("EXACT_ADDRESS_DELETE", "VALIDATED"),
    ]
    state = build_capability_state(core_only)
    assert state.primitive_validated is True
    assert state.production_recovery_validated is False


def test_a_broken_primitive_is_reported_as_such():
    state = build_capability_state(
        _all_validated(EPHEMERAL_ADDRESS_CREATE="FAILED")
    )
    assert state.primitive_validated is False
    assert state.production_recovery_validated is False
