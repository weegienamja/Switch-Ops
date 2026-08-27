"""Gate 3: what is strong enough to authorise creating a specific address?

Almost every test here is about something being *rejected*. That is the shape of
the problem: the failure mode is not "we could not find an address", it is "we
convinced ourselves an address was free". Silence, staleness, a plausible label,
or a reservation borrowed from somewhere else must all fail closed, so those
cases outnumber the accepting ones by a wide margin.

Everything is synthetic: documentation-space addresses (RFC 5737 and RFC 2544),
invented attestors, invented network ids.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from backend.app.recovery_execution import (
    DISPOSABLE_ONLY_AUTHORITY,
    REJECTED_COLLISION_EVIDENCE,
    REQUIRED_ATTESTOR,
    RecoveryAddressReservation,
    assess_recovery_reservation,
)

NOW = datetime(2026, 8, 27, 12, 0, tzinfo=timezone.utc)
PREFIX = "192.0.2.0/24"
CANDIDATE = "192.0.2.250"
TARGET = "192.0.2.10"
GATEWAY = "192.0.2.1"


def _reservation(**overrides) -> RecoveryAddressReservation:
    authority = overrides.get("authority", "OPERATOR_DECLARED")
    payload = {
        "address": CANDIDATE,
        "prefixLength": 24,
        "managementPrefix": PREFIX,
        "authority": authority,
        "attestorType": REQUIRED_ATTESTOR[authority],
        "attestedBy": "synthetic network owner",
        "scope": "PRODUCTION_NETWORK",
        "networkScopeId": "synthetic-management-vlan",
        "evidenceReference": "synthetic-change-record-0001",
        "declaredAt": NOW - timedelta(hours=2),
        "reservedUntil": NOW + timedelta(hours=2),
    }
    payload.update(overrides)
    return RecoveryAddressReservation.model_validate(payload)


def _assess(reservation, **overrides):
    kwargs = {
        "candidate_address": CANDIDATE,
        "management_prefix": PREFIX,
        "target_address": TARGET,
        "gateway_address": GATEWAY,
        "local_addresses": [],
        "now": NOW,
    }
    kwargs.update(overrides)
    return assess_recovery_reservation(reservation, **kwargs)


# --- authority quality: absence of evidence is never authority -------------

def test_no_reservation_is_refused_rather_than_resolved():
    result = _assess(None)
    assert result.usable is False
    assert result.blockers == ["NO_RESERVATION"]


@pytest.mark.parametrize(
    "weak",
    [
        "ICMP_SILENCE",
        "STALE_ARP_ABSENCE",
        "ABSENT_FROM_LOCAL_NEIGHBOR_CACHE",
        "UNUSED_IN_LAST_OBSERVED_MAC_TABLE",
        "APPARENTLY_UNUSED_ADDRESS",
        "FREE_LOOKING_DHCP_RANGE",
        "DISCOVERY_CONFIDENCE",
        "MODEL_INFERENCE",
        "NETWORK_DESCRIPTION",
        "DAD_FOUND_NO_DUPLICATE",
    ],
)
def test_probe_and_inference_evidence_is_named_as_rejected(weak):
    # These are not merely absent from the accepted list. They are enumerated as
    # rejected so nobody has to guess whether they were overlooked.
    assert weak in REJECTED_COLLISION_EVIDENCE


def test_no_rejected_evidence_type_is_also_an_authority_type():
    authorities = set(REQUIRED_ATTESTOR)
    assert authorities.isdisjoint(set(REJECTED_COLLISION_EVIDENCE))


def test_an_operator_guess_cannot_be_expressed_as_an_authority_type():
    # There is deliberately no authority value for "the operator thinks it is
    # free". The type system is where that distinction is enforced.
    with pytest.raises(Exception):
        _reservation(authority="OPERATOR_THINKS_IT_LOOKS_FREE")


@pytest.mark.parametrize("attestor", ["", "  ", "na"])
def test_an_anonymous_attestor_is_refused(attestor):
    with pytest.raises(Exception):
        _reservation(attestedBy=attestor)


def test_a_reservation_missing_its_expiry_cannot_be_constructed():
    payload = _reservation().model_dump(by_alias=True)
    payload.pop("reservedUntil")
    with pytest.raises(Exception):
        RecoveryAddressReservation.model_validate(payload)


def test_a_malformed_address_is_refused_as_malformed():
    result = _assess(_reservation(address="not-an-address"),
                     candidate_address="not-an-address")
    assert result.usable is False
    assert "RESERVATION_MALFORMED" in result.blockers


def test_an_ipv6_reservation_is_refused():
    result = _assess(_reservation(address="2001:db8::1"),
                     candidate_address="2001:db8::1")
    assert result.usable is False
    assert "RESERVATION_MALFORMED" in result.blockers


# --- scope: authority over one address, one network, one operation ---------

def test_a_complete_declaration_for_the_exact_candidate_is_accepted():
    result = _assess(_reservation())
    assert result.usable is True
    assert result.blockers == []


def test_a_reservation_for_a_different_address_authorises_nothing():
    result = _assess(_reservation(address="192.0.2.251"))
    assert result.usable is False
    assert "RESERVATION_ADDRESS_MISMATCH" in result.blockers


def test_a_reservation_carrying_a_different_prefix_length_is_refused():
    result = _assess(_reservation(prefixLength=25))
    assert result.usable is False
    assert "RESERVATION_PREFIX_LENGTH_MISMATCH" in result.blockers


def test_a_reservation_for_another_network_is_refused():
    result = _assess(_reservation(), management_prefix="198.51.100.0/24")
    assert result.usable is False
    assert "RESERVATION_OUTSIDE_MANAGEMENT_PREFIX" in result.blockers


def test_a_reservation_naming_another_environment_is_refused():
    result = _assess(
        _reservation(), expected_network_scope_id="a-different-vlan"
    )
    assert result.usable is False
    assert "RESERVATION_SCOPE_MISMATCH" in result.blockers


def test_a_lab_reservation_cannot_authorise_a_production_address():
    lab = _reservation(
        authority="LAB_HARNESS_RESERVED", scope="DISPOSABLE_LAB_ENVIRONMENT"
    )
    result = _assess(lab, expected_scope="PRODUCTION_NETWORK")
    assert result.usable is False
    assert "RESERVATION_AUTHORITY_UNSUPPORTED" in result.blockers
    assert "RESERVATION_SCOPE_MISMATCH" in result.blockers


def test_lab_authority_is_declared_as_disposable_only():
    assert DISPOSABLE_ONLY_AUTHORITY == ("LAB_HARNESS_RESERVED",)


def test_a_production_reservation_is_not_silently_valid_in_the_lab():
    result = _assess(_reservation(), expected_scope="DISPOSABLE_LAB_ENVIRONMENT")
    assert result.usable is False
    assert "RESERVATION_SCOPE_MISMATCH" in result.blockers


def test_the_default_scope_is_production_so_a_forgetful_caller_fails_closed():
    lab = _reservation(
        authority="LAB_HARNESS_RESERVED", scope="DISPOSABLE_LAB_ENVIRONMENT"
    )
    # No expected_scope passed at all.
    result = assess_recovery_reservation(
        lab,
        candidate_address=CANDIDATE,
        management_prefix=PREFIX,
        target_address=TARGET,
        gateway_address=GATEWAY,
        local_addresses=[],
        now=NOW,
    )
    assert result.usable is False


def test_a_reservation_bound_to_another_operation_cannot_be_replayed():
    result = _assess(
        _reservation(planBinding="operation-a"),
        expected_plan_binding="operation-b",
    )
    assert result.usable is False
    assert "RESERVATION_BINDING_MISMATCH" in result.blockers


def test_a_bound_reservation_is_refused_when_no_operation_is_named():
    result = _assess(_reservation(planBinding="operation-a"))
    assert result.usable is False
    assert "RESERVATION_BINDING_MISMATCH" in result.blockers


def test_a_bound_reservation_authorises_its_own_operation():
    result = _assess(
        _reservation(planBinding="operation-a"), expected_plan_binding="operation-a"
    )
    assert result.usable is True


# --- freshness -------------------------------------------------------------

def test_an_expired_reservation_is_no_authority_at_all():
    result = _assess(_reservation(reservedUntil=NOW - timedelta(minutes=1)))
    assert result.usable is False
    assert "RESERVATION_EXPIRED" in result.blockers


def test_a_reservation_expiring_exactly_now_has_already_closed():
    result = _assess(_reservation(reservedUntil=NOW))
    assert result.usable is False
    assert "RESERVATION_EXPIRED" in result.blockers


def test_a_reservation_dated_in_the_future_is_refused():
    result = _assess(
        _reservation(
            declaredAt=NOW + timedelta(hours=1),
            reservedUntil=NOW + timedelta(hours=5),
        )
    )
    assert result.usable is False
    assert "RESERVATION_NOT_YET_VALID" in result.blockers


def test_a_reservation_that_expires_before_it_was_declared_is_malformed():
    result = _assess(
        _reservation(
            declaredAt=NOW - timedelta(hours=1),
            reservedUntil=NOW - timedelta(hours=2),
        )
    )
    assert result.usable is False
    assert "RESERVATION_MALFORMED" in result.blockers


def test_an_ancient_attestation_needs_re_attesting_even_if_unexpired():
    # Two independent limits: the expiry the attestor set, and how long any
    # attestation may stand before somebody looks at it again.
    result = _assess(
        _reservation(
            declaredAt=NOW - timedelta(days=500),
            reservedUntil=NOW + timedelta(days=500),
        )
    )
    assert result.usable is False
    assert "RESERVATION_EVIDENCE_STALE" in result.blockers


# --- structural safety, whatever the authority says ------------------------

@pytest.mark.parametrize("address", ["192.0.2.0", "192.0.2.255"])
def test_network_and_broadcast_addresses_are_refused(address):
    result = _assess(_reservation(address=address), candidate_address=address)
    assert result.usable is False
    assert "RESERVATION_IS_NETWORK_OR_BROADCAST" in result.blockers


def test_the_gateway_cannot_be_reserved_however_authoritatively():
    result = _assess(_reservation(address=GATEWAY), candidate_address=GATEWAY)
    assert result.usable is False
    assert "RESERVATION_IS_GATEWAY_ADDRESS" in result.blockers


def test_the_target_device_address_cannot_be_reserved():
    result = _assess(_reservation(address=TARGET), candidate_address=TARGET)
    assert result.usable is False
    assert "RESERVATION_IS_TARGET_ADDRESS" in result.blockers


def test_an_address_this_host_already_holds_is_refused():
    result = _assess(_reservation(), local_addresses=[CANDIDATE])
    assert result.usable is False
    assert "RESERVATION_CONFLICTS_WITH_LOCAL_ADDRESS" in result.blockers


def test_an_address_another_recovery_operation_owns_is_refused():
    result = _assess(_reservation(), owned_addresses=[CANDIDATE])
    assert result.usable is False
    assert "RESERVATION_CONFLICTS_WITH_OWNED_ADDRESS" in result.blockers


def test_an_address_outside_the_prefix_is_refused():
    result = _assess(
        _reservation(address="198.51.100.5"), candidate_address="198.51.100.5"
    )
    assert result.usable is False
    assert "RESERVATION_OUTSIDE_MANAGEMENT_PREFIX" in result.blockers


def test_a_malformed_management_prefix_is_refused():
    result = _assess(_reservation(), management_prefix="not-a-prefix")
    assert result.usable is False
    assert "RESERVATION_MALFORMED" in result.blockers


# --- authority types cannot masquerade as one another ----------------------

@pytest.mark.parametrize("authority", sorted(REQUIRED_ATTESTOR))
def test_each_authority_type_requires_its_own_attestor(authority):
    scope = (
        "DISPOSABLE_LAB_ENVIRONMENT"
        if authority in DISPOSABLE_ONLY_AUTHORITY
        else "PRODUCTION_NETWORK"
    )
    good = _assess(
        _reservation(authority=authority, scope=scope), expected_scope=scope
    )
    assert good.usable is True, authority

    wrong = next(
        value for value in REQUIRED_ATTESTOR.values()
        if value != REQUIRED_ATTESTOR[authority]
    )
    bad = _assess(
        _reservation(authority=authority, scope=scope, attestorType=wrong),
        expected_scope=scope,
    )
    assert bad.usable is False
    assert "RESERVATION_ATTESTOR_INVALID" in bad.blockers


def test_an_operator_declaration_cannot_be_filed_as_an_ipam_record():
    result = _assess(
        _reservation(authority="INFRASTRUCTURE_ATTESTED", attestorType="NAMED_OPERATOR")
    )
    assert result.usable is False
    assert "RESERVATION_ATTESTOR_INVALID" in result.blockers


def test_a_dhcp_exclusion_cannot_be_filed_as_an_operator_declaration():
    result = _assess(
        _reservation(
            authority="OPERATOR_DECLARED", attestorType="DHCP_SERVICE_RECORD"
        )
    )
    assert result.usable is False
    assert "RESERVATION_ATTESTOR_INVALID" in result.blockers


def test_every_authority_type_has_exactly_one_permitted_attestor():
    assert len(set(REQUIRED_ATTESTOR.values())) == len(REQUIRED_ATTESTOR)


# --- several faults at once ------------------------------------------------

def test_a_reservation_wrong_in_several_ways_reports_all_of_them():
    result = _assess(
        _reservation(
            address="198.51.100.7",
            attestorType="IPAM_RECORD",
            reservedUntil=NOW - timedelta(minutes=1),
        ),
        candidate_address=CANDIDATE,
    )
    assert result.usable is False
    for code in (
        "RESERVATION_ADDRESS_MISMATCH",
        "RESERVATION_ATTESTOR_INVALID",
        "RESERVATION_OUTSIDE_MANAGEMENT_PREFIX",
        "RESERVATION_EXPIRED",
    ):
        assert code in result.blockers, code


# --- privacy ---------------------------------------------------------------

def test_the_fixtures_carry_no_machine_specific_values():
    # The values, not the file: a source scan would trip over the very patterns
    # it is looking for. Every address here must be documentation space and
    # every identifier must be invented.
    import ipaddress
    import re

    payload = _reservation().model_dump(by_alias=True, mode="json")
    rendered = " ".join(str(value) for value in payload.values())

    assert re.search(r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}", rendered) is None
    assert "recovery-env-" not in rendered

    documentation = ipaddress.ip_network("192.0.2.0/24")
    for address in (CANDIDATE, TARGET, GATEWAY):
        assert ipaddress.ip_address(address) in documentation, address
    assert ipaddress.ip_network(PREFIX) == documentation
