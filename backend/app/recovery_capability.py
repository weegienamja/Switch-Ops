"""What has actually been proven about the recovery primitive, capability by capability.

An isolated experiment on 2026-08-27 showed that the Windows IP Helper
primitive can create an
ephemeral IPv4 address, settle real (non-optimistic) DAD to preferred, honour an
explicit on-link prefix length, delete exactly that address, and leave the
interface as it was found. That is a genuine result and this module records it.

It is also a narrow one. The experiment ran on a statically addressed isolated
adapter. It says nothing about whether the same call is safe on an interface
whose primary IPv4 address is controlled by DHCP -- which is the only
configuration production recovery would ever run in.

So capabilities are tracked separately and production readiness is the
*conjunction* of them. Proving one can never imply another by accident, because
there is no single field to flip.
"""

from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field

#: One thing that either has or has not been observed to work.
RecoveryCapability = Literal[
    "EPHEMERAL_ADDRESS_CREATE",
    "DUPLICATE_ADDRESS_DETECTION",
    "EXPLICIT_ON_LINK_PREFIX",
    "EXACT_ADDRESS_DELETE",
    "ROLLBACK_RESTORES_BASELINE",
    "CRASH_OWNERSHIP_RECONCILIATION",
    "DHCP_SAME_INTERFACE_COEXISTENCE",
    #: Gate 3. Whether the reservation-authority chain that must precede any
    #: address creation has actually been exercised end to end, rather than
    #: merely unit tested. Having code that can evaluate an attestation is not
    #: the same as having measured that the chain refuses and permits correctly.
    "COLLISION_SAFE_ADDRESS_AUTHORITY",
    "PRODUCTION_ADAPTER_CLASS",
]

CapabilityStatus = Literal["VALIDATED", "UNVALIDATED", "NOT_ATTEMPTED", "FAILED"]

#: Where a capability was observed. A result from a statically addressed
#: adapter does not carry over to a DHCP-controlled one, so the environment is
#: part of the evidence rather than a footnote.
EvidenceEnvironment = Literal[
    "ISOLATED_STATIC_ADAPTER",
    "DISPOSABLE_DHCP_ADAPTER",
    "PRODUCTION_ADAPTER",
    "NONE",
]

#: Compatibility class rather than a specific build number. Pinning "build
#: 26200" into product logic would make every future Windows update look like a
#: regression, which is not what the evidence supports.
PlatformClass = Literal["WINDOWS_10_OR_LATER_X64", "UNKNOWN"]

#: Capabilities that must all be VALIDATED before a recovery could be offered on
#: a real, DHCP-controlled production interface.
#:
#: ``PRODUCTION_ADAPTER_CLASS`` is a member on purpose, and its absence was a
#: real hole: every measurement so far was taken on a disposable virtual
#: adapter, so without it, validating the one remaining capability would have
#: flipped ``production_recovery_validated`` to True while no production adapter
#: had ever been touched. A field named for production must not claim more
#: production evidence than exists. That it can only be satisfied by evidence
#: this project has deliberately never gathered is the point, not a dead end.
PRODUCTION_REQUIRED_CAPABILITIES: tuple[RecoveryCapability, ...] = (
    "EPHEMERAL_ADDRESS_CREATE",
    "DUPLICATE_ADDRESS_DETECTION",
    "EXPLICIT_ON_LINK_PREFIX",
    "EXACT_ADDRESS_DELETE",
    "ROLLBACK_RESTORES_BASELINE",
    "CRASH_OWNERSHIP_RECONCILIATION",
    "DHCP_SAME_INTERFACE_COEXISTENCE",
    "COLLISION_SAFE_ADDRESS_AUTHORITY",
    "PRODUCTION_ADAPTER_CLASS",
)


class CapabilityEvidence(BaseModel):
    """One capability, its status, and where that was observed."""

    capability: RecoveryCapability
    status: CapabilityStatus
    environment: EvidenceEnvironment
    observed_at: datetime | None = Field(default=None, alias="observedAt")
    detail: str = ""

    model_config = {"populate_by_name": True}


class RecoveryPrimitiveCapability(BaseModel):
    """The primitive's proven surface, and what that does and does not license."""

    primitive: Literal["IP_HELPER_EPHEMERAL_UNICAST"] = "IP_HELPER_EPHEMERAL_UNICAST"
    platform_class: PlatformClass = Field(alias="platformClass")
    capabilities: list[CapabilityEvidence]

    #: True when the low-level primitive works at all on this platform.
    primitive_validated: bool = Field(alias="primitiveValidated")
    #: True only when every production-required capability is validated. This is
    #: deliberately a separate field: a reader who checks the wrong one gets a
    #: conservative answer rather than an optimistic one.
    production_recovery_validated: bool = Field(alias="productionRecoveryValidated")
    unvalidated_for_production: list[RecoveryCapability] = Field(
        alias="unvalidatedForProduction"
    )
    summary: str

    model_config = {"populate_by_name": True}


def build_capability_state(
    evidence: list[CapabilityEvidence],
    *,
    platform_class: PlatformClass = "WINDOWS_10_OR_LATER_X64",
) -> RecoveryPrimitiveCapability:
    """Derive both verdicts from per-capability evidence.

    ``primitive_validated`` asks whether the mechanism works at all.
    ``production_recovery_validated`` asks whether it has been shown safe in the
    configuration production would use. The second is never inferred from the
    first.
    """
    by_capability = {item.capability: item for item in evidence}

    def validated(capability: RecoveryCapability) -> bool:
        found = by_capability.get(capability)
        return bool(found and found.status == "VALIDATED")

    # "The mechanism works" is the create/DAD/delete core.
    primitive_ok = all(
        validated(capability)
        for capability in (
            "EPHEMERAL_ADDRESS_CREATE",
            "DUPLICATE_ADDRESS_DETECTION",
            "EXACT_ADDRESS_DELETE",
        )
    )

    missing = [
        capability
        for capability in PRODUCTION_REQUIRED_CAPABILITIES
        if not validated(capability)
    ]

    if missing:
        summary = (
            f"The primitive is {'validated' if primitive_ok else 'not yet validated'} "
            f"on this platform, but {len(missing)} capability/capabilities remain "
            "unproven for a DHCP-controlled production interface."
        )
    else:
        summary = (
            "Every production-required capability has been observed, including "
            "coexistence with a DHCP-controlled primary address."
        )

    return RecoveryPrimitiveCapability(
        platformClass=platform_class,
        capabilities=evidence,
        primitiveValidated=primitive_ok,
        productionRecoveryValidated=not missing,
        unvalidatedForProduction=missing,
        summary=summary,
    )


def dhcp_coexistence_validated() -> bool:
    """Has the primitive been shown to coexist with a DHCP-controlled primary?

    Recovery planning uses this to decide whether the Windows DHCP/static
    coexistence setting is relevant at all. It is not: that setting governs the
    standard configuration API, and the measured primitive adds a separate
    unicast row without touching the DHCP lease. Answering the narrow question
    here keeps planning from having to reason about the capability record.
    """
    return any(
        item.capability == "DHCP_SAME_INTERFACE_COEXISTENCE"
        and item.status == "VALIDATED"
        for item in current_capability_state().capabilities
    )


def current_capability_state() -> RecoveryPrimitiveCapability:
    """The capability state as actually evidenced today.

    Each validated entry names the environment its measurement was taken in, and
    no result stands in for another: the isolated static adapter answered the
    create/DAD/delete core, and the disposable DHCP adapter separately answered
    coexistence, reservation authority, and crash ownership. Nothing here was
    measured on a production adapter, which is why
    ``PRODUCTION_ADAPTER_CLASS`` is still the one capability holding
    ``production_recovery_validated`` at False.
    """
    observed = datetime.fromisoformat("2026-08-27T00:00:00+00:00")
    coexistence_observed = datetime.fromisoformat("2026-08-27T02:00:00+00:00")
    # Read from the Recovery Lab reservation record the successful Gate 3 run
    # left behind, not chosen by hand. It is the instant the run captured when
    # it started, which the runner also stamped on the reservation it released,
    # so it identifies the run to the second rather than marking its completion.
    # The two earlier gates predate that record and carry hand-entered dates;
    # see the provenance tests in `test_gate3_measured_evidence`.
    authority_observed = datetime.fromisoformat("2026-08-27T10:48:01.805216+00:00")
    # Same provenance rule for the crash experiment: read from the reservation
    # the reconciling process released, never typed. Phase B releases that
    # reservation only after the delete, the post-delete absence re-proof, the
    # exact DHCP primary check, and both baseline fingerprints have all passed,
    # so the *existence* of this stamp is the durable record that reconciliation
    # succeeded. The *value* is the instant the reconciling process read its own
    # clock on startup, which identifies the run rather than timing its last
    # write; the journal close that follows records a reason but no time of its
    # own. See `test_crash_ownership_measured_evidence`.
    crash_observed = datetime.fromisoformat("2026-08-27T15:05:50.033140+00:00")
    isolated = "ISOLATED_STATIC_ADAPTER"
    return build_capability_state(
        [
            CapabilityEvidence(
                capability="EPHEMERAL_ADDRESS_CREATE",
                status="VALIDATED",
                environment=isolated,
                observedAt=observed,
                detail="The ephemeral address was created on an isolated adapter.",
            ),
            CapabilityEvidence(
                capability="DUPLICATE_ADDRESS_DETECTION",
                status="VALIDATED",
                environment=isolated,
                observedAt=observed,
                detail="Real DAD settled tentative to preferred in about 3.5 seconds.",
            ),
            CapabilityEvidence(
                capability="EXPLICIT_ON_LINK_PREFIX",
                status="VALIDATED",
                environment=isolated,
                observedAt=observed,
                detail="An explicit /24 was honoured rather than the initialised /32.",
            ),
            CapabilityEvidence(
                capability="EXACT_ADDRESS_DELETE",
                status="VALIDATED",
                environment=isolated,
                observedAt=observed,
                detail="Deletion targeted exactly the created row and was confirmed absent.",
            ),
            CapabilityEvidence(
                capability="ROLLBACK_RESTORES_BASELINE",
                status="VALIDATED",
                environment=isolated,
                observedAt=observed,
                detail="No unrelated state changed and the baseline fingerprint was restored.",
            ),
            CapabilityEvidence(
                capability="CRASH_OWNERSHIP_RECONCILIATION",
                status="VALIDATED",
                environment="DISPOSABLE_DHCP_ADAPTER",
                observedAt=crash_observed,
                detail=(
                    "Measured on a disposable DHCP adapter, elevated, within a "
                    "single Windows boot. One process took a fresh "
                    "harness-owned reservation, captured the DHCP baseline, "
                    "persisted intent, created exactly one temporary RFC 5737 "
                    "address, persisted its post-apply identity, saw real "
                    "duplicate address detection reach Preferred, and then "
                    "terminated through the crash path with exit code 89, so "
                    "no rollback, reservation release or journal close ran. "
                    "Windows then showed both rows at once: the DHCP primary "
                    "still DHCP/DHCP and Preferred, and 192.0.2.251/24 as an "
                    "independent MANUAL/MANUAL row that had outlived its "
                    "creator. A second, unrelated process reconstructed "
                    "ownership from durable state alone, re-proved the adapter "
                    "against the recorded GUID with a matching interface LUID "
                    "and index, matched every ownership predicate on the exact "
                    "row including the Windows CreationTimeStamp recorded "
                    "before the crash, adjudicated DELETE_AUTHORISED, deleted "
                    "exactly once, re-proved the row absent, and confirmed the "
                    "original DHCP primary and both the addressing and network "
                    "baselines intact before releasing the reservation and "
                    "closing the journal to zero outstanding records. Same "
                    "boot only: this is process death, not reboot, machine "
                    "crash, power loss, NIC reset, driver restart or adapter "
                    "recreation, and not a production adapter."
                ),
            ),
            CapabilityEvidence(
                capability="DHCP_SAME_INTERFACE_COEXISTENCE",
                status="VALIDATED",
                environment="DISPOSABLE_DHCP_ADAPTER",
                observedAt=coexistence_observed,
                detail=(
                    "On a disposable VirtualBox DHCP adapter, a temporary RFC 5737 "
                    "address reached Preferred after real duplicate address "
                    "detection and coexisted as an independent MANUAL/MANUAL row. "
                    "The DHCP-controlled primary stayed DHCP/DHCP and Preferred "
                    "with a finite lease still counting down; the requested /24 "
                    "on-link route appeared; default routes and DNS were "
                    "unchanged; and exact-row deletion restored the baseline."
                ),
            ),
            CapabilityEvidence(
                capability="COLLISION_SAFE_ADDRESS_AUTHORITY",
                status="VALIDATED",
                environment="DISPOSABLE_DHCP_ADAPTER",
                observedAt=authority_observed,
                detail=(
                    "Measured twice on a disposable DHCP adapter, elevated. With "
                    "no reservation the run refused with AUTHORITY_ABSENT and "
                    "created nothing. With a fresh harness-owned reservation "
                    "bound to the exact candidate, prefix, environment and run, "
                    "authority passed before any mutation, exactly one temporary "
                    "RFC 5737 address was created, real duplicate address "
                    "detection reached Preferred, the DHCP primary and its "
                    "finite lease were preserved, exact-row deletion restored "
                    "the baseline, and the reservation was released. This is "
                    "the mechanism working; it is not a production reservation."
                ),
            ),
            CapabilityEvidence(
                capability="PRODUCTION_ADAPTER_CLASS",
                status="NOT_ATTEMPTED",
                environment="NONE",
                detail="No experiment has been run on a production adapter, by design.",
            ),
        ]
    )
