from __future__ import annotations

from datetime import datetime, timedelta, timezone
import inspect

import pytest


from app.management_path import (
    HostAddressObservation,
    LastKnownManagementPath,
    ManagementPathDiagnosis,
    ManagementPathObservation,
    ManagementRoute,
)
from app.meraki_management import MerakiManagementEvidence
from app import recovery_plan as recovery_module
from app.recovery_execution import RecoveryAddressReservation
from app.recovery_plan import (
    CandidateAddressEvidence,
    build_recovery_plan,
    validate_recovery_plan_binding,
)


NOW = datetime(2026, 8, 26, 9, 0, tzinfo=timezone.utc)
TARGET = "198.18.10.10"


def current_path(
    *,
    adapter_id: str = "adapter-ethernet",
    source: str = "198.18.20.5",
    gateway: str = "198.18.20.1",
    route_kind: str = "default",
    route_prefix: str = "0.0.0.0/0",
    next_hop: str | None = "198.18.20.1",
    on_prefix: bool = False,
    coexistence: bool | None = True,
    addresses: list[HostAddressObservation] | None = None,
    observed_at: datetime = NOW,
) -> ManagementPathObservation:
    return ManagementPathObservation(
        observedAt=observed_at,
        adapterId=adapter_id,
        adapterName="Synthetic Ethernet",
        interfaceIndex=12,
        interfaceMetric=25,
        adapterState="Up",
        sourceIp=source,
        prefixLength=24,
        connectedPrefix="198.18.20.0/24",
        targetOnConnectedPrefix=on_prefix,
        dhcpEnabled=True,
        dhcpStaticCoexistence=coexistence,
        adapterAddresses=addresses
        or [
            HostAddressObservation(
                address=source,
                prefixLength=24,
                prefixOrigin="Dhcp",
                addressState="Preferred",
                skipAsSource=False,
            )
        ],
        dhcpServer=gateway,
        dhcpLeaseObtained=NOW - timedelta(minutes=5),
        defaultGateway=gateway,
        route=ManagementRoute(
            destinationPrefix=route_prefix,
            nextHop=next_hop,
            kind=route_kind,
            routeMetric=0,
            protocol="NetMgmt",
        ),
        windowsConnectivity="Internet",
        tcp22="timed_out",
        icmpReachable=False,
    )


def historical_path(
    *,
    same_adapter: bool = True,
    observed_at: datetime = NOW - timedelta(minutes=10),
) -> LastKnownManagementPath:
    return LastKnownManagementPath(
        observedAt=observed_at,
        lastDeviceSuccessAt=observed_at,
        adapterId="adapter-ethernet",
        adapterName="Synthetic Ethernet",
        sourceIp="198.18.10.95",
        prefixLength=24,
        connectedPrefix="198.18.10.0/24",
        defaultGateway="198.18.10.1",
        sameAdapterAsCurrent=same_adapter,
        provenance=["synthetic-management-path-history"],
    )


def diagnosis(conclusion: str = "HOST_NETWORK_CHANGED") -> ManagementPathDiagnosis:
    return ManagementPathDiagnosis(
        conclusion=conclusion,
        confidence="HIGH" if conclusion == "HOST_NETWORK_CHANGED" else "INDETERMINATE",
        headline="Synthetic management-path assessment",
        summary="Synthetic evidence used to exercise the deterministic planner.",
    )


def meraki() -> MerakiManagementEvidence:
    return MerakiManagementEvidence(
        state="healthy",
        checkedAt=NOW,
        observedAt=NOW,
        freshness="current",
        complete=True,
        detail="Synthetic current configuration.",
    )


def candidate(
    *, assurance: str = "authoritative-reservation"
) -> CandidateAddressEvidence:
    return CandidateAddressEvidence(
        address="198.18.10.200",
        prefixLength=24,
        assurance=assurance,
        source="synthetic authoritative reservation",
        observedAt=NOW,
    )


def reservation(address: str = "198.18.10.200", **overrides) -> RecoveryAddressReservation:
    """A complete authoritative declaration for the synthetic candidate.

    The planner no longer accepts the candidate's own `assurance` label as
    authority, so a plan that is meant to reach READY has to carry the
    attestation the label claims exists.
    """
    payload = {
        "address": address,
        "prefixLength": 24,
        "managementPrefix": "198.18.10.0/24",
        "authority": "OPERATOR_DECLARED",
        "attestorType": "NAMED_OPERATOR",
        "attestedBy": "synthetic network owner",
        "scope": "PRODUCTION_NETWORK",
        "networkScopeId": "synthetic-management-vlan",
        "evidenceReference": "synthetic-change-record-0001",
        "declaredAt": NOW - timedelta(days=1),
        "reservedUntil": NOW + timedelta(days=7),
    }
    payload.update(overrides)
    return RecoveryAddressReservation.model_validate(payload)


def plan(**overrides):
    arguments = {
        "target": TARGET,
        "current": current_path(),
        "last_known_good": historical_path(),
        "diagnosis": diagnosis(),
        "meraki": meraki(),
        "candidate": candidate(),
        "reservation": reservation(),
        "now": NOW,
    }
    arguments.update(overrides)
    return build_recovery_plan(**arguments)


def blocker_codes(result) -> set[str]:
    return {item.code for item in result.blockers}


def test_host_move_with_reserved_candidate_produces_ready_secondary_plan() -> None:
    result = plan()

    assert result.status == "READY"
    assert result.kind == "TEMPORARY_SECONDARY_IPV4"
    assert result.operation.candidate_address == "198.18.10.200"
    assert result.operation.gateway is None
    assert result.operation.expected_route == "198.18.10.0/24"
    assert result.operation.adapter_id == "adapter-ethernet"
    assert result.execution_enabled is False
    assert result.candidate_evidence is not None
    assert result.execution_architecture.gate.allowed is False
    assert result.execution_architecture.gate.disposition == "NOT_IMPLEMENTED"
    assert result.execution_architecture.primitive.selected_primitive == "NONE"
    assert result.execution_architecture.authority.future_policy_ceiling == "OPERATOR_APPROVED"


def test_default_route_without_collision_safe_candidate_is_blocked() -> None:
    result = plan(candidate=None)

    assert result.status == "BLOCKED"
    assert "COLLISION_SAFE_ADDRESS_UNAVAILABLE" in blocker_codes(result)
    assert result.operation.candidate_address is None
    assert "PLAN_BLOCKER:COLLISION_SAFE_ADDRESS_UNAVAILABLE" in (
        result.execution_architecture.gate.reasons
    )


def test_unverified_candidate_does_not_treat_failed_reachability_as_collision_proof() -> None:
    result = plan(candidate=candidate(assurance="unverified"))

    assert result.status == "BLOCKED"
    assert "CANDIDATE_ADDRESS_INVALID" in blocker_codes(result)


@pytest.mark.parametrize("coexistence,code", [
    (False, "DHCP_STATIC_COEXISTENCE_DISABLED"),
    (None, "DHCP_STATIC_COEXISTENCE_UNVERIFIED"),
])
def test_dhcp_primary_is_protected_while_coexistence_is_unmeasured(
    coexistence, code, monkeypatch
) -> None:
    # Before the primitive was measured against a DHCP-controlled interface,
    # the Windows coexistence setting was the safest signal available.
    monkeypatch.setattr(
        recovery_module, "dhcp_coexistence_validated", lambda: False
    )
    result = plan(current=current_path(coexistence=coexistence), candidate=None)

    assert result.status == "BLOCKED"
    assert code in blocker_codes(result)
    assert "Keep the DHCP primary address and current default gateway." in result.unchanged_state


@pytest.mark.parametrize("coexistence", [False, None, True])
def test_the_windows_coexistence_setting_stops_blocking_once_measured(
    coexistence,
) -> None:
    # The setting governs the standard configuration API. The measured
    # primitive adds a separate unicast row and leaves the lease intact, so the
    # setting no longer decides whether recovery is possible.
    result = plan(current=current_path(coexistence=coexistence), candidate=None)

    codes = blocker_codes(result)
    assert "DHCP_STATIC_COEXISTENCE_DISABLED" not in codes
    assert "DHCP_STATIC_COEXISTENCE_UNVERIFIED" not in codes
    # It still protects the DHCP primary in the plan's stated invariants.
    assert "Keep the DHCP primary address and current default gateway." in result.unchanged_state


def test_a_validated_coexistence_capability_grants_no_collision_authority() -> None:
    # Gate 2 says the primitive is safe alongside DHCP. It says nothing about
    # whether any particular address is free to use, so with no authoritative
    # reservation the plan stays blocked.
    result = plan(current=current_path(coexistence=True), candidate=None)

    assert result.status == "BLOCKED"
    assert "COLLISION_SAFE_ADDRESS_UNAVAILABLE" in blocker_codes(result)


def test_same_subnet_or_explicit_route_never_proposes_an_address() -> None:
    same_subnet = current_path(
        source="198.18.10.95",
        gateway="198.18.10.1",
        route_kind="connected",
        route_prefix="198.18.10.0/24",
        next_hop="0.0.0.0",
        on_prefix=True,
    )
    explicit = current_path(
        route_kind="scoped",
        route_prefix="198.18.10.0/24",
        next_hop="198.18.20.254",
    )

    for current in (same_subnet, explicit):
        result = plan(
            current=current,
            diagnosis=diagnosis("DEVICE_OR_PATH_UNREACHABLE"),
        )
        assert result.status == "NOT_SUPPORTED"
        assert result.kind == "NONE"


def test_existing_compatible_secondary_address_prevents_a_duplicate_operation() -> None:
    addresses = [
        HostAddressObservation(
            address="198.18.20.5",
            prefixLength=24,
            prefixOrigin="Dhcp",
            addressState="Preferred",
            skipAsSource=False,
        ),
        HostAddressObservation(
            address="198.18.10.95",
            prefixLength=24,
            prefixOrigin="Manual",
            addressState="Preferred",
            skipAsSource=False,
        ),
    ]
    result = plan(current=current_path(addresses=addresses))

    assert result.status == "NOT_SUPPORTED"
    assert "COMPATIBLE_SECONDARY_ADDRESS_PRESENT" in blocker_codes(result)
    assert result.kind == "NONE"


def test_historical_adapter_binding_distinguishes_other_active_adapters() -> None:
    wrong_adapter = plan(last_known_good=historical_path(same_adapter=False))
    intended_ethernet = plan(last_known_good=historical_path(same_adapter=True))

    assert "MANAGEMENT_ADAPTER_UNVERIFIED" in blocker_codes(wrong_adapter)
    assert wrong_adapter.status == "BLOCKED"
    assert intended_ethernet.status == "READY"
    assert intended_ethernet.operation.adapter_id == "adapter-ethernet"


def test_stale_or_missing_history_blocks_recovery() -> None:
    stale = plan(
        last_known_good=historical_path(observed_at=NOW - timedelta(days=8))
    )
    missing_prefix = historical_path()
    missing_prefix.connected_prefix = None
    missing_prefix.management_prefix = None
    absent = plan(last_known_good=missing_prefix)

    assert "HISTORICAL_EVIDENCE_STALE" in blocker_codes(stale)
    assert "HISTORICAL_MANAGEMENT_PREFIX_MISSING" in blocker_codes(absent)
    assert stale.status == absent.status == "BLOCKED"


def test_stale_current_observation_blocks_recovery() -> None:
    result = plan(current=current_path(observed_at=NOW - timedelta(minutes=3)))
    assert result.status == "BLOCKED"
    assert "CURRENT_OBSERVATION_STALE" in blocker_codes(result)


@pytest.mark.parametrize(
    "conclusion,status",
    [
        ("AUTHENTICATION_FAILED", "NOT_SUPPORTED"),
        ("HOST_KEY_CHANGED", "NOT_SUPPORTED"),
        ("SSH_NEGOTIATION_FAILED", "NOT_SUPPORTED"),
        ("INDETERMINATE", "BLOCKED"),
    ],
)
def test_non_address_failure_classes_do_not_create_address_recovery(
    conclusion, status
) -> None:
    result = plan(diagnosis=diagnosis(conclusion))

    assert result.status == status
    assert result.kind == "NONE"
    assert result.operation.candidate_address is None


def test_healthy_path_needs_no_recovery() -> None:
    result = plan(diagnosis=diagnosis("MANAGEMENT_PATH_HEALTHY"))
    assert result.status == "NOT_NEEDED"
    assert result.kind == "NONE"


def test_device_unreachable_despite_valid_connected_path_is_not_address_recovery() -> None:
    connected = current_path(
        source="198.18.10.95",
        gateway="198.18.10.1",
        route_kind="connected",
        route_prefix="198.18.10.0/24",
        next_hop="0.0.0.0",
        on_prefix=True,
    )
    result = plan(
        current=connected,
        diagnosis=diagnosis("DEVICE_OR_PATH_UNREACHABLE"),
    )
    assert result.status == "NOT_SUPPORTED"
    assert "VALID_MANAGEMENT_PATH_ALREADY_EXISTS" in blocker_codes(result)


@pytest.mark.parametrize(
    "changed,expected_field",
    [
        ({"target": "198.18.10.11"}, "target"),
        ({"current": current_path(source="198.18.20.6")}, "primary_address"),
        ({"current": current_path(gateway="198.18.20.254")}, "default_gateway"),
        ({"current": current_path(adapter_id="adapter-wifi")}, "adapter"),
        (
            {
                "current": current_path().model_copy(
                    update={"dhcp_lease_obtained": NOW}
                )
            },
            "dhcp_lease",
        ),
        (
            {
                "current": current_path().model_copy(
                    update={
                        "route": current_path().route.model_copy(
                            update={"route_metric": 50}
                        )
                    }
                )
            },
            "route",
        ),
        (
            {"current": current_path(observed_at=NOW + timedelta(seconds=1))},
            "evidence_observed_at",
        ),
    ],
)
def test_plan_binding_invalidates_relevant_state_changes(changed, expected_field) -> None:
    result = plan()
    inputs = {
        "target": TARGET,
        "current": current_path(),
        "diagnosis": diagnosis(),
    }
    inputs.update(changed)

    validation = validate_recovery_plan_binding(result, **inputs)
    assert validation.valid is False
    assert expected_field in validation.changed_fields


def test_plan_binding_validates_the_exact_same_state() -> None:
    result = plan()
    validation = validate_recovery_plan_binding(
        result,
        target=TARGET,
        current=current_path(),
        diagnosis=diagnosis(),
    )
    assert validation.valid is True
    assert validation.changed_fields == []


def test_planner_module_has_no_local_network_mutation_or_shell_runtime() -> None:
    source = inspect.getsource(recovery_module)
    for forbidden in (
        "subprocess.run",
        "os.system",
        "New-NetIPAddress",
        "Set-NetIPAddress",
        "Remove-NetIPAddress",
        "New-NetRoute",
        "Remove-NetRoute",
    ):
        assert forbidden not in source


# --- Gate 3: the candidate's own label is not authority --------------------

def test_a_candidate_without_a_reservation_is_not_collision_authorised() -> None:
    """The `assurance` field is the caller's word for it, not an attestation.

    Before Gate 3 the planner accepted the label on its own, which meant any
    caller could promote a guess to an authoritative reservation by spelling it
    correctly.
    """
    result = plan(reservation=None)

    assert result.status == "BLOCKED"
    assert "COLLISION_SAFE_ADDRESS_UNAVAILABLE" in blocker_codes(result)
    assert result.operation.candidate_address is None


def test_a_reservation_for_a_different_address_does_not_authorise_the_candidate() -> None:
    result = plan(reservation=reservation(address="198.18.10.201"))

    assert result.status == "BLOCKED"
    assert "COLLISION_SAFE_ADDRESS_UNAVAILABLE" in blocker_codes(result)


def test_an_expired_reservation_does_not_authorise_the_candidate() -> None:
    result = plan(reservation=reservation(reservedUntil=NOW - timedelta(minutes=1)))

    assert result.status == "BLOCKED"
    assert "COLLISION_SAFE_ADDRESS_UNAVAILABLE" in blocker_codes(result)


def test_a_lab_reservation_does_not_authorise_a_production_candidate() -> None:
    # Real authority inside a disposable environment, none at all here.
    result = plan(
        reservation=reservation(
            authority="LAB_HARNESS_RESERVED",
            attestorType="LAB_HARNESS",
            scope="DISPOSABLE_LAB_ENVIRONMENT",
        )
    )

    assert result.status == "BLOCKED"
    assert "COLLISION_SAFE_ADDRESS_UNAVAILABLE" in blocker_codes(result)


def test_an_attestor_that_does_not_match_its_authority_is_refused() -> None:
    result = plan(reservation=reservation(attestorType="IPAM_RECORD"))

    assert result.status == "BLOCKED"
    assert "COLLISION_SAFE_ADDRESS_UNAVAILABLE" in blocker_codes(result)


def test_a_complete_authoritative_reservation_authorises_the_candidate() -> None:
    result = plan()

    assert result.status == "READY"
    assert result.operation.candidate_address == "198.18.10.200"
    # READY still means "may ask", never "may act".
    assert result.execution_enabled is False


def test_the_refusal_explains_why_the_reservation_did_not_authorise() -> None:
    result = plan(reservation=reservation(address="198.18.10.201"))

    joined = " ".join(result.missing_evidence)
    assert "198.18.10.201" in joined
    assert "198.18.10.200" in joined


def test_gate_three_does_not_make_the_planner_an_executor() -> None:
    result = plan()

    assert result.execution_enabled is False
    assert result.execution_architecture.executor_implemented is False
    assert result.execution_architecture.approval_available is False


# --- a validated Gate 3 mechanism is not a production reservation ----------

def test_a_validated_gate_three_still_blocks_a_plan_with_no_reservation() -> None:
    """The capability is green and the plan is still blocked, on purpose.

    Gate 3 measured that SwitchOps can require, validate, bind and consume
    reservation evidence. Refusing when there is nothing to consume is the
    behaviour that was measured, so a green capability has to make this blocker
    more trustworthy, never absent.
    """
    from app.recovery_capability import current_capability_state

    entry = next(
        item
        for item in current_capability_state().capabilities
        if item.capability == "COLLISION_SAFE_ADDRESS_AUTHORITY"
    )
    assert entry.status == "VALIDATED"

    result = plan(candidate=None, reservation=None)

    assert result.status == "BLOCKED"
    assert "COLLISION_SAFE_ADDRESS_UNAVAILABLE" in blocker_codes(result)
    assert result.operation.candidate_address is None
    assert result.execution_enabled is False


def test_the_disposable_authority_that_passed_gate_three_is_refused_here() -> None:
    # Exactly the reservation shape the successful isolated run consumed.
    result = plan(
        reservation=reservation(
            authority="LAB_HARNESS_RESERVED",
            attestorType="LAB_HARNESS",
            scope="DISPOSABLE_LAB_ENVIRONMENT",
            networkScopeId="synthetic-environment-0001",
        )
    )

    assert result.status == "BLOCKED"
    assert "COLLISION_SAFE_ADDRESS_UNAVAILABLE" in blocker_codes(result)


def test_a_production_scoped_reservation_passes_the_collision_authority_stage() -> None:
    # The other side of the same boundary: valid production-scoped authority is
    # accepted, subject to every other readiness requirement.
    result = plan()

    assert "COLLISION_SAFE_ADDRESS_UNAVAILABLE" not in blocker_codes(result)
    assert result.operation.candidate_address == "198.18.10.200"
    assert result.execution_enabled is False
