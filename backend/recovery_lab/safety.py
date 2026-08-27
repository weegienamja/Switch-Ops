"""Which interfaces and addresses the harness is permitted to touch.

The harness exists to learn how a recovery primitive behaves. Learning that on
the interface carrying the operator's only working network path would be a poor
trade, so eligibility is decided from positive evidence and the default answer
is no.
"""

from __future__ import annotations

import ipaddress
from dataclasses import dataclass, field
from typing import Literal, Sequence

#: RFC 5737 documentation ranges. The harness will not create anything outside
#: them, so an experiment can never collide with real addressing.
DOCUMENTATION_NETWORKS = tuple(
    ipaddress.ip_network(value)
    for value in ("192.0.2.0/24", "198.51.100.0/24", "203.0.113.0/24")
)

EligibilityBlocker = Literal[
    "PLATFORM_UNSUPPORTED",
    "ELEVATION_UNAVAILABLE",
    "INTERFACE_NOT_FOUND",
    "INTERFACE_IDENTITY_NOT_RESOLVED",
    "INTERFACE_CARRIES_DEFAULT_ROUTE",
    "INTERFACE_HAS_DHCP_LEASE",
    "DHCP_TEST_AUTHORITY_REQUIRED",
    "INTERFACE_DHCP_LEASE_REQUIRED",
    "INTERFACE_NOT_EXPLICITLY_ALLOWED",
    "ADDRESS_NOT_DOCUMENTATION_RANGE",
    "ADDRESS_ALREADY_PRESENT",
    "PREFIX_LENGTH_INVALID",
]


@dataclass(frozen=True)
class InterfaceFacts:
    """What the harness needs to know about a candidate interface."""

    interface_index: int
    interface_luid: int
    alias: str
    carries_default_route: bool
    has_dhcp_lease: bool


@dataclass(frozen=True)
class EligibilityDecision:
    eligible: bool
    blockers: list[EligibilityBlocker] = field(default_factory=list)
    evidence: list[str] = field(default_factory=list)


def is_documentation_address(address: str) -> bool:
    try:
        parsed = ipaddress.ip_address(address)
    except ValueError:
        return False
    return any(parsed in network for network in DOCUMENTATION_NETWORKS)


def assess_target(
    *,
    interface: InterfaceFacts | None,
    address: str,
    prefix_length: int,
    allowed_interfaces: Sequence[str],
    platform_supported: bool,
    elevated: bool,
    existing_addresses: Sequence[str] = (),
    dhcp_test_authority: bool = False,
    dhcp_coexistence_requested: bool = False,
) -> EligibilityDecision:
    """Decide whether one experiment may run against one interface.

    ``allowed_interfaces`` must be supplied by the operator. There is no
    heuristic that promotes an interface to "probably fine": an adapter is
    eligible because somebody named it, or it is not eligible.
    """
    blockers: list[EligibilityBlocker] = []
    evidence: list[str] = []

    if not platform_supported:
        blockers.append("PLATFORM_UNSUPPORTED")
        evidence.append("The IP Helper unicast APIs exist on Windows only.")
    if not elevated:
        blockers.append("ELEVATION_UNAVAILABLE")
        evidence.append(
            "CreateUnicastIpAddressEntry requires Administrators; unelevated "
            "callers receive ERROR_ACCESS_DENIED."
        )

    if interface is None:
        blockers.append("INTERFACE_NOT_FOUND")
        evidence.append("The named interface was not present.")
    else:
        if interface.interface_luid <= 0:
            blockers.append("INTERFACE_IDENTITY_NOT_RESOLVED")
            evidence.append(
                f"{interface.alias!r} has no stable interface LUID; an exact "
                "journal-owned rollback could not be guaranteed."
            )
        if interface.alias not in set(allowed_interfaces):
            blockers.append("INTERFACE_NOT_EXPLICITLY_ALLOWED")
            evidence.append(
                f"{interface.alias!r} was not named as an allowed test interface."
            )
        if interface.carries_default_route:
            # The operator's working path. Never.
            blockers.append("INTERFACE_CARRIES_DEFAULT_ROUTE")
            evidence.append(
                f"{interface.alias!r} carries a default route and is production."
            )
        if interface.has_dhcp_lease and not dhcp_test_authority:
            # A DHCP-managed interface is normally the one whose DHCP behaviour
            # we must not perturb while learning. The single exception is an
            # adapter this harness provisioned itself and can still recognise,
            # which is what dhcp_test_authority attests. It is deliberately not
            # a flag the caller can simply pass: see environment.py.
            blockers.append("INTERFACE_HAS_DHCP_LEASE")
            evidence.append(
                f"{interface.alias!r} holds a DHCP lease and carries no disposable "
                "test provenance; experiments run on adapters this harness created."
            )
        elif interface.has_dhcp_lease:
            evidence.append(
                f"{interface.alias!r} is DHCP-controlled and carries disposable "
                "test provenance, so DHCP coexistence may be measured on it."
            )

        if dhcp_coexistence_requested and not dhcp_test_authority:
            blockers.append("DHCP_TEST_AUTHORITY_REQUIRED")
            evidence.append(
                "DHCP coexistence requires a currently proven disposable "
                "environment; --allow is not authority."
            )
        if dhcp_coexistence_requested and not interface.has_dhcp_lease:
            blockers.append("INTERFACE_DHCP_LEASE_REQUIRED")
            evidence.append(
                f"{interface.alias!r} has no live DHCP row, so it cannot answer "
                "the same-interface DHCP coexistence question."
            )

    if not is_documentation_address(address):
        blockers.append("ADDRESS_NOT_DOCUMENTATION_RANGE")
        evidence.append(
            f"{address} is outside RFC 5737 documentation space."
        )
    if not 0 < prefix_length <= 32:
        blockers.append("PREFIX_LENGTH_INVALID")
        evidence.append("Prefix length must be between 1 and 32.")
    if address in set(existing_addresses):
        # If it is already there, it is not ours, and rollback could not tell
        # the difference afterwards.
        blockers.append("ADDRESS_ALREADY_PRESENT")
        evidence.append(f"{address} already exists and would not be ours to remove.")

    return EligibilityDecision(
        eligible=not blockers, blockers=blockers, evidence=evidence
    )
