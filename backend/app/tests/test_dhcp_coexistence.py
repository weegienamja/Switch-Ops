"""DHCP coexistence measurement, and the authority that gates it.

Two things are being pinned here. First, that "preserved" means the primary is
still *DHCP-controlled with a live lease*, not merely that the same string
appears in the address table. Second, that allowing a DHCP interface for this
one experiment did not quietly make the operator's production adapter eligible.
"""
from __future__ import annotations

from datetime import datetime, timezone
import pytest

from backend.recovery_lab.coexistence import NetworkSnapshot, evaluate_dhcp_coexistence
from backend.recovery_lab.safety import InterfaceFacts, assess_target
from backend.recovery_lab.windows_unicast import UnicastAddress

NOW = datetime(2026, 8, 27, 12, 0, 0, tzinfo=timezone.utc)
PRIMARY = "192.168.57.101"       # DHCP-served by the disposable environment
TEMPORARY = "192.0.2.250"        # RFC 5737, the simulated management prefix
TEST_ADAPTER = "Ethernet 3"


def _dhcp_row(address=PRIMARY, *, prefix=24, dad="PREFERRED", lifetime=3600,
              origin=("DHCP", "DHCP")):
    return UnicastAddress(
        address=address, prefix_length=prefix, interface_index=20, interface_luid=0,
        prefix_origin=origin[0], suffix_origin=origin[1], dad_state=dad,
        valid_lifetime=lifetime, preferred_lifetime=lifetime, skip_as_source=False,
    )


def _manual_row(address=TEMPORARY, *, prefix=24, dad="PREFERRED",
                origin=("MANUAL", "MANUAL")):
    return UnicastAddress(
        address=address, prefix_length=prefix, interface_index=20, interface_luid=0,
        prefix_origin=origin[0], suffix_origin=origin[1], dad_state=dad,
        valid_lifetime=0xFFFFFFFF, preferred_lifetime=0xFFFFFFFF, skip_as_source=False,
    )


BASELINE_PRIMARY = _dhcp_row(lifetime=3600)


def _snapshot(**overrides) -> NetworkSnapshot:
    payload = {
        "interface_addresses": ((PRIMARY, 24),),
        "interface_routes": ("192.168.57.0/24",),
        "default_routes": ((16, "203.0.113.1"),),
        "dns_servers": ("208.67.222.222",),
        "source_selection": (("198.51.100.7", PRIMARY),),
    }
    payload.update(overrides)
    return NetworkSnapshot(**payload)


def _evaluate(observed=None, before=None, after=None, elapsed=5.0):
    return evaluate_dhcp_coexistence(
        primary_address=PRIMARY,
        primary_prefix_length=24,
        temporary_address=TEMPORARY,
        temporary_prefix_length=24,
        baseline_primary=BASELINE_PRIMARY,
        observed=observed if observed is not None else [_dhcp_row(lifetime=3595), _manual_row()],
        before=before or _snapshot(),
        after=after or _snapshot(
            interface_addresses=((PRIMARY, 24), (TEMPORARY, 24)),
            interface_routes=("192.168.57.0/24", "192.0.2.0/24"),
        ),
        expected_on_link_prefix="192.0.2.0/24",
        elapsed_seconds=elapsed,
    )


# --- the passing shape -----------------------------------------------------

def test_a_clean_coexistence_run_is_preserved():
    result = _evaluate()
    assert result.preserved is True, result.findings
    assert result.findings == []
    joined = " ".join(result.evidence)
    assert "still DHCP/DHCP" in joined
    assert "counting down" in joined


def test_a_naturally_decreasing_lease_is_not_a_failure():
    # Leases count down. Equality would fail every real run.
    result = _evaluate(observed=[_dhcp_row(lifetime=3400), _manual_row()])
    assert result.preserved is True


# --- primary preservation --------------------------------------------------

def test_a_primary_that_stopped_being_dhcp_fails_even_though_the_ip_is_present():
    # The EnableStatic failure mode: same address, no longer a lease.
    result = _evaluate(
        observed=[_dhcp_row(origin=("MANUAL", "MANUAL")), _manual_row()]
    )
    assert result.preserved is False
    assert "PRIMARY_NO_LONGER_DHCP" in result.findings


def test_a_primary_with_an_infinite_lifetime_has_lost_its_lease():
    result = _evaluate(observed=[_dhcp_row(lifetime=0xFFFFFFFF), _manual_row()])
    assert result.preserved is False
    assert "PRIMARY_LEASE_LOST" in result.findings


def test_a_missing_primary_fails():
    result = _evaluate(observed=[_manual_row()])
    assert result.preserved is False
    assert "PRIMARY_ADDRESS_MISSING" in result.findings


def test_a_primary_knocked_out_of_preferred_fails():
    result = _evaluate(observed=[_dhcp_row(dad="TENTATIVE"), _manual_row()])
    assert result.preserved is False
    assert "PRIMARY_NOT_PREFERRED" in result.findings


def test_a_primary_whose_prefix_changed_fails():
    result = _evaluate(observed=[_dhcp_row(prefix=16), _manual_row()])
    assert result.preserved is False
    assert "PRIMARY_PREFIX_CHANGED" in result.findings


def test_a_lease_that_jumps_up_implausibly_is_reported():
    # Suggests the address was released and re-acquired rather than left alone.
    result = _evaluate(observed=[_dhcp_row(lifetime=99999), _manual_row()], elapsed=5.0)
    assert result.preserved is False
    assert "PRIMARY_LEASE_EXTENDED_IMPLAUSIBLY" in result.findings


# --- temporary address -----------------------------------------------------

def test_a_missing_temporary_address_fails():
    result = _evaluate(observed=[_dhcp_row(lifetime=3595)])
    assert result.preserved is False
    assert "TEMPORARY_ADDRESS_MISSING" in result.findings


def test_a_tentative_temporary_address_fails():
    result = _evaluate(observed=[_dhcp_row(lifetime=3595), _manual_row(dad="TENTATIVE")])
    assert result.preserved is False
    assert "TEMPORARY_ADDRESS_NOT_PREFERRED" in result.findings


def test_a_slash_32_temporary_address_fails():
    result = _evaluate(observed=[_dhcp_row(lifetime=3595), _manual_row(prefix=32)])
    assert result.preserved is False
    assert "TEMPORARY_PREFIX_WRONG" in result.findings


def test_a_temporary_address_adopted_by_dhcp_fails():
    result = _evaluate(
        observed=[_dhcp_row(lifetime=3595), _manual_row(origin=("DHCP", "DHCP"))]
    )
    assert result.preserved is False
    assert "TEMPORARY_ADDRESS_IS_DHCP" in result.findings


# --- collateral ------------------------------------------------------------

def test_a_changed_default_route_fails():
    result = _evaluate(after=_snapshot(
        interface_routes=("192.168.57.0/24", "192.0.2.0/24"),
        default_routes=((18, "203.0.113.1"),),
    ))
    assert result.preserved is False
    assert "DEFAULT_ROUTE_CHANGED" in result.findings


def test_changed_dns_fails():
    result = _evaluate(after=_snapshot(
        interface_routes=("192.168.57.0/24", "192.0.2.0/24"),
        dns_servers=("198.51.100.53",),
    ))
    assert result.preserved is False
    assert "DNS_CHANGED" in result.findings


def test_a_missing_on_link_route_fails():
    # Without it the temporary address would not reach the management prefix.
    result = _evaluate(after=_snapshot(interface_routes=("192.168.57.0/24",)))
    assert result.preserved is False
    assert "ON_LINK_ROUTE_MISSING" in result.findings


def test_a_lost_preexisting_route_fails():
    result = _evaluate(after=_snapshot(interface_routes=("192.0.2.0/24",)))
    assert result.preserved is False
    assert "UNRELATED_ROUTE_CHANGED" in result.findings


def test_changed_source_selection_for_unrelated_traffic_fails():
    # Adding a management address must not redirect ordinary traffic.
    result = _evaluate(after=_snapshot(
        interface_routes=("192.168.57.0/24", "192.0.2.0/24"),
        source_selection=(("198.51.100.7", TEMPORARY),),
    ))
    assert result.preserved is False
    assert "SOURCE_SELECTION_CHANGED" in result.findings


def test_source_selection_toward_the_management_prefix_is_not_a_regression():
    # A *new* destination using the temporary source is the desired behaviour.
    result = _evaluate(after=_snapshot(
        interface_routes=("192.168.57.0/24", "192.0.2.0/24"),
        source_selection=(("198.51.100.7", PRIMARY), ("192.0.2.10", TEMPORARY)),
    ))
    assert result.preserved is True, result.findings


# --- the guard did not get weaker ------------------------------------------

def test_production_ethernet_stays_refused_even_with_dhcp_authority_granted():
    # The whole risk of this stage: allowing DHCP interfaces in general.
    production = InterfaceFacts(
        interface_index=16, interface_luid=0x1600000000000000, alias="Ethernet",
        carries_default_route=True, has_dhcp_lease=True,
    )
    decision = assess_target(
        interface=production, address=TEMPORARY, prefix_length=24,
        allowed_interfaces=["Ethernet"], platform_supported=True, elevated=True,
        dhcp_test_authority=True,
    )
    assert decision.eligible is False
    assert "INTERFACE_CARRIES_DEFAULT_ROUTE" in decision.blockers


def test_an_arbitrary_dhcp_interface_without_authority_is_refused():
    other = InterfaceFacts(
        interface_index=30, interface_luid=0x1E00000000000000, alias="Ethernet 9",
        carries_default_route=False, has_dhcp_lease=True,
    )
    decision = assess_target(
        interface=other, address=TEMPORARY, prefix_length=24,
        allowed_interfaces=["Ethernet 9"], platform_supported=True, elevated=True,
        dhcp_test_authority=False,
    )
    assert decision.eligible is False
    assert "INTERFACE_HAS_DHCP_LEASE" in decision.blockers


def test_a_disposable_dhcp_interface_with_authority_is_eligible():
    disposable = InterfaceFacts(
        interface_index=20, interface_luid=0x1400000000000000, alias=TEST_ADAPTER,
        carries_default_route=False, has_dhcp_lease=True,
    )
    decision = assess_target(
        interface=disposable, address=TEMPORARY, prefix_length=24,
        allowed_interfaces=[TEST_ADAPTER], platform_supported=True, elevated=True,
        dhcp_test_authority=True,
    )
    assert decision.eligible is True, decision.blockers
