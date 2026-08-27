"""Gate 3: does a reservation-authority prerequisite actually gate the mutation?

Gates 1 and 2 measured what the Windows primitive does. Gate 3 measures
something different and, for a product that will one day touch a real network,
more important: that nothing is created unless a positive, in-scope, unexpired
attestation names the exact candidate address first.

The experiment therefore adds a prerequisite rather than a mechanism. Once
authority is established it *delegates* the mutation to the already-proven Gate 2
runner instead of reimplementing create/DAD/verify/rollback, because a second
implementation would be a second thing to get wrong and would prove nothing new.

Two independent controls run in series and neither substitutes for the other:

* **Authority** answers "may we use this exact address?" -- checked before any
  mutation, by evidence that exists independently of the network.
* **DAD** answers "did Windows see a duplicate when we created it?" -- checked
  by the operating system, at creation time, on the wire.

Authority without DAD would trust a record over reality. DAD without authority
would turn "nothing objected" into "we are allowed", which is precisely the
inference this gate exists to make impossible.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Literal, Sequence

from .reservation import LabReservationRegistry, to_product_reservation

Gate3Outcome = Literal[
    "SUCCESS",
    # Refusals that must happen with zero address creates.
    "ENVIRONMENT_NOT_AUTHORISED",
    "AUTHORITY_ABSENT",
    "AUTHORITY_STALE",
    "AUTHORITY_INVALID",
    "AUTHORITY_SCOPE_MISMATCH",
    "CANDIDATE_NOT_RESERVED",
    "CANDIDATE_STRUCTURALLY_UNSAFE",
    # Reality disagreed with the record after a create was attempted.
    "AUTHORITY_CONTRADICTED_BY_DAD",
    # Anything the delegated Gate 2 runner reported.
    "COEXISTENCE_FAILED",
]

#: Which refusal each product-assessor blocker maps to. Written out rather than
#: inferred so that a new blocker cannot silently fall into a permissive default.
_BLOCKER_OUTCOME: dict[str, Gate3Outcome] = {
    "NO_RESERVATION": "AUTHORITY_ABSENT",
    "RESERVATION_EXPIRED": "AUTHORITY_STALE",
    "RESERVATION_EVIDENCE_STALE": "AUTHORITY_STALE",
    "RESERVATION_NOT_YET_VALID": "AUTHORITY_STALE",
    "RESERVATION_SCOPE_MISMATCH": "AUTHORITY_SCOPE_MISMATCH",
    "RESERVATION_AUTHORITY_UNSUPPORTED": "AUTHORITY_SCOPE_MISMATCH",
    "RESERVATION_BINDING_MISMATCH": "AUTHORITY_SCOPE_MISMATCH",
    "RESERVATION_ADDRESS_MISMATCH": "CANDIDATE_NOT_RESERVED",
    "RESERVATION_ATTESTOR_INVALID": "AUTHORITY_INVALID",
    "RESERVATION_MALFORMED": "AUTHORITY_INVALID",
    "RESERVATION_OUTSIDE_MANAGEMENT_PREFIX": "CANDIDATE_STRUCTURALLY_UNSAFE",
    "RESERVATION_PREFIX_LENGTH_MISMATCH": "CANDIDATE_STRUCTURALLY_UNSAFE",
    "RESERVATION_IS_TARGET_ADDRESS": "CANDIDATE_STRUCTURALLY_UNSAFE",
    "RESERVATION_IS_GATEWAY_ADDRESS": "CANDIDATE_STRUCTURALLY_UNSAFE",
    "RESERVATION_IS_NETWORK_OR_BROADCAST": "CANDIDATE_STRUCTURALLY_UNSAFE",
    "RESERVATION_CONFLICTS_WITH_LOCAL_ADDRESS": "CANDIDATE_STRUCTURALLY_UNSAFE",
    "RESERVATION_CONFLICTS_WITH_OWNED_ADDRESS": "CANDIDATE_STRUCTURALLY_UNSAFE",
}

#: Refusals ordered by how fundamental they are, so a reservation that is wrong
#: in several ways is reported by its most basic fault rather than by whichever
#: check happened to run first.
_OUTCOME_PRIORITY: tuple[Gate3Outcome, ...] = (
    "AUTHORITY_ABSENT",
    "AUTHORITY_INVALID",
    "AUTHORITY_SCOPE_MISMATCH",
    "CANDIDATE_NOT_RESERVED",
    "AUTHORITY_STALE",
    "CANDIDATE_STRUCTURALLY_UNSAFE",
)


@dataclass
class Gate3Result:
    outcome: Gate3Outcome
    steps: list[tuple[str, str, str]] = field(default_factory=list)
    evidence: list[str] = field(default_factory=list)
    authority_blockers: list[str] = field(default_factory=list)
    reservation_id: str = ""
    operation_id: str = ""
    dad_state: str = "ABSENT"
    #: Address creates actually attempted. Every refusal path must leave this 0.
    creates_attempted: int = 0
    #: True when nothing was created, or when what was created is confirmed gone
    #: and the baseline is confirmed back.
    restored: bool = False
    coexistence_outcome: str | None = None

    def step(self, name: str, status: str, detail: str = "") -> None:
        self.steps.append((status, name, detail))


def _refusal(result: Gate3Result, blockers: Sequence[str]) -> Gate3Result:
    """Turn assessor blockers into one refusal, without creating anything."""
    outcomes = {_BLOCKER_OUTCOME.get(code, "AUTHORITY_INVALID") for code in blockers}
    for candidate in _OUTCOME_PRIORITY:
        if candidate in outcomes:
            result.outcome = candidate
            break
    else:  # pragma: no cover - _BLOCKER_OUTCOME covers every declared blocker
        result.outcome = "AUTHORITY_INVALID"
    result.authority_blockers = list(blockers)
    result.creates_attempted = 0
    result.restored = True  # nothing was attempted, so nothing needs undoing
    return result


def evaluate_gate3_authority(
    *,
    candidate_address: str,
    target_prefix: str,
    environment_id: str,
    run_id: str,
    registry: LabReservationRegistry,
    now: datetime,
    gateway_address: str | None = None,
    local_addresses: Sequence[str] = (),
    owned_addresses: Sequence[str] = (),
    target_address: str = "",
):
    """Decide whether a lab reservation authorises this exact create.

    The lab supplies a record; the *product* assessor decides what it is worth.
    Keeping the decision on the product side means the harness cannot bless its
    own attestation, and means the refusals measured here are the same ones a
    production reservation would face.
    """
    from backend.app.recovery_execution import (
        RecoveryAddressReservation,
        assess_recovery_reservation,
    )

    found = registry.find(
        address=candidate_address, environment_id=environment_id, now=now
    )
    if found is None:
        # Either nothing was reserved, or what was reserved has expired or been
        # released. The registry does not distinguish, and neither should this:
        # all three mean there is no live claim about this address.
        return None, assess_recovery_reservation(
            None,
            candidate_address=candidate_address,
            management_prefix=target_prefix,
            target_address=target_address,
            gateway_address=gateway_address,
            local_addresses=local_addresses,
            now=now,
            expected_scope="DISPOSABLE_LAB_ENVIRONMENT",
        )

    try:
        reservation = RecoveryAddressReservation.model_validate(
            to_product_reservation(found)
        )
    except Exception:
        from backend.app.recovery_execution import ReservationAssessment

        return found, ReservationAssessment(
            usable=False,
            blockers=["RESERVATION_MALFORMED"],
            evidence=[
                "The stored reservation does not satisfy the product reservation "
                "schema, so it cannot authorise anything."
            ],
        )

    assessment = assess_recovery_reservation(
        reservation,
        candidate_address=candidate_address,
        management_prefix=target_prefix,
        target_address=target_address,
        gateway_address=gateway_address,
        local_addresses=local_addresses,
        now=now,
        expected_scope="DISPOSABLE_LAB_ENVIRONMENT",
        expected_network_scope_id=environment_id,
        expected_plan_binding=run_id,
        owned_addresses=owned_addresses,
    )
    return found, assessment


def run_gate3_experiment(
    *,
    interface_index: int,
    interface_luid: int,
    interface_alias: str,
    candidate_address: str,
    target_prefix: str,
    prefix_length: int,
    environment_id: str | None,
    environment_authority_granted: bool,
    run_id: str,
    registry: LabReservationRegistry,
    journal,
    read_table,
    read_snapshot,
    create,
    delete,
    now: datetime,
    gateway_address: str | None = None,
    target_address: str = "",
    sleep=None,
    dad_timeout: float | None = None,
) -> Gate3Result:
    """Prove authority first, then delegate the mutation to the Gate 2 runner.

    Ordering is the whole experiment. Every authority check happens before the
    first create, so a refusal costs nothing and a create means authority was
    genuinely established beforehand.
    """
    from .coexistence import run_dhcp_coexistence_experiment

    result = Gate3Result(outcome="ENVIRONMENT_NOT_AUTHORISED")

    # --- 1. environment identity (Gate 2's question, not Gate 3's) ----------
    #
    # Reservation authority says nothing about which interface we are on, so it
    # cannot stand in for environment ownership. Both are required.
    if not environment_authority_granted or not environment_id:
        result.step("environment-authority", "FAIL", "no disposable environment proven")
        result.evidence.append(
            "Gate 3 may only run on an adapter proven to belong to a disposable "
            "Recovery Lab environment. A reservation does not make an interface "
            "ours."
        )
        result.restored = True
        return result
    result.step("environment-authority", "PASS", "disposable environment proven")

    # --- 2-9. reservation authority, entirely before any mutation -----------
    local = [row.address for row in read_table()]
    owned = [item.address for item in journal.outstanding()]
    found, assessment = evaluate_gate3_authority(
        candidate_address=candidate_address,
        target_prefix=target_prefix,
        environment_id=environment_id,
        run_id=run_id,
        registry=registry,
        now=now,
        gateway_address=gateway_address,
        local_addresses=local,
        owned_addresses=owned,
        target_address=target_address,
    )
    result.evidence.extend(assessment.evidence)
    if found is not None:
        result.reservation_id = found.reservation_id
    if not assessment.usable:
        result.step("reservation-authority", "FAIL", ", ".join(assessment.blockers))
        return _refusal(result, assessment.blockers)
    result.step(
        "reservation-authority",
        "PASS",
        f"{candidate_address} reserved in {environment_id}",
    )

    # Bind the reservation to this run before anything exists, so a crash leaves
    # a record that authorises this operation and no future one.
    registry.bind(found.reservation_id, run_id)
    result.step("reservation-binding", "PASS", run_id)

    # --- 10-19. the already-proven mechanism, unchanged ---------------------
    #
    # Gate 2 established that this sequence creates, settles real DAD, honours
    # the requested prefix, preserves a DHCP primary and its surroundings, and
    # deletes exactly its own row. Gate 3 adds a prerequisite to it rather than
    # a second copy of it.
    kwargs = dict(
        interface_index=interface_index,
        interface_luid=interface_luid,
        interface_alias=interface_alias,
        temporary_address=candidate_address,
        prefix_length=prefix_length,
        expected_on_link_prefix=target_prefix,
        authority_granted=True,
        journal=journal,
        read_table=read_table,
        read_snapshot=read_snapshot,
        create=create,
        delete=delete,
        plan_id=run_id,
    )
    if sleep is not None:
        kwargs["sleep"] = sleep
    if dad_timeout is not None:
        kwargs["dad_timeout"] = dad_timeout
    coexistence = run_dhcp_coexistence_experiment(**kwargs)

    result.coexistence_outcome = coexistence.outcome
    result.operation_id = coexistence.operation_id
    result.dad_state = coexistence.dad_state
    result.creates_attempted = coexistence.creates_attempted
    result.restored = coexistence.restored
    result.steps.extend(coexistence.steps)
    result.evidence.extend(coexistence.evidence)

    # --- 20. release, whatever happened ------------------------------------
    #
    # The reservation covered one run. Leaving it open would let the next run
    # inherit authority it never established.
    registry.release(found.reservation_id, now=now)
    result.step("reservation-release", "PASS", found.reservation_id)

    if coexistence.outcome == "DAD_DUPLICATE":
        # The record said the address was reserved; the wire said somebody has
        # it. That is a contradiction to report, not an address to reinterpret
        # as safe, and not a reason to try a different one.
        result.outcome = "AUTHORITY_CONTRADICTED_BY_DAD"
        result.evidence.append(
            "A reserved address was reported as a duplicate by Windows. The "
            "reservation and the network disagree; the reservation is not "
            "evidence that the network is wrong."
        )
        return result

    result.outcome = "SUCCESS" if coexistence.outcome == "SUCCESS" else "COEXISTENCE_FAILED"
    return result
