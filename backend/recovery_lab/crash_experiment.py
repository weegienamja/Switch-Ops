"""Phase A: create a temporary address, then die without cleaning up.

Everything before the last line of this module is the ordinary, careful Gate 3
sequence: prove the environment, prove the reservation, bind it, capture a
baseline, write durable intent, create exactly one row, record what that row
turned out to be. The last line then destroys the process.

The crash has to be real to measure anything. An exception would run ``finally``
blocks and context managers; a ``return`` would let the caller roll back; a
signal handler could be caught. ``os._exit`` skips all of it -- no ``finally``,
no ``atexit``, no destructors, no buffer flushing -- so what survives is exactly
what was already flushed to the operating system, which is the durability claim
under test. The kernel then releases the operation lock, which is how the next
process proves it is recovering from a dead run rather than racing a live one.

The one thing done before dying is flushing stdout, so the operator can see how
far the run got. That is a report, not cleanup: nothing about the address, the
journal or the reservation is tidied.

This module is Recovery Lab only. Nothing under ``backend.app`` imports it, and
it refuses to run at all unless the interface is proven to belong to a
harness-owned disposable environment.
"""
from __future__ import annotations

import ipaddress
import os
import sys
from dataclasses import dataclass, field
from datetime import datetime
from typing import Literal

#: Distinctive on purpose. A shell seeing this knows the process died where it
#: was told to, rather than failing for some ordinary reason.
CRASH_EXIT_CODE = 89

#: RFC 5737. The crash experiment leaves an address behind between two
#: processes, so it may only ever leave behind one that cannot belong to a real
#: network.
DOCUMENTATION_NETWORKS = (
    ipaddress.ip_network("192.0.2.0/24"),
    ipaddress.ip_network("198.51.100.0/24"),
    ipaddress.ip_network("203.0.113.0/24"),
)

PhaseAOutcome = Literal[
    #: Reached the crash point. The process does not return this; it dies.
    "CRASHED_AS_INTENDED",
    "ENVIRONMENT_NOT_AUTHORISED",
    "CANDIDATE_NOT_DOCUMENTATION_SPACE",
    "AUTHORITY_ABSENT",
    "AUTHORITY_REFUSED",
    "INTERFACE_CARRIES_DEFAULT_ROUTE",
    "BASELINE_INCOMPLETE",
    "OPERATION_ALREADY_HELD",
    "OPERATION_LOCK_FAILURE",
    "RESERVATION_BINDING_FAILED",
    "JOURNAL_PERSISTENCE_FAILURE",
    "ADDRESS_CREATE_FAILURE",
    "POST_APPLY_EVIDENCE_UNAVAILABLE",
    "DAD_NOT_PREFERRED",
]


@dataclass
class PhaseAResult:
    outcome: PhaseAOutcome
    steps: list[tuple[str, str, str]] = field(default_factory=list)
    evidence: list[str] = field(default_factory=list)
    operation_id: str = ""
    reservation_id: str = ""
    dad_state: str = "ABSENT"
    creates_attempted: int = 0
    deletes_attempted: int = 0
    #: True only when the row is confirmed gone again. Phase A never sets this:
    #: leaving the address behind is the entire point.
    restored: bool = False
    #: The lock proving this operation is still held. On the crash path it is
    #: never released here -- the kernel does that when the process dies, which
    #: is how the next process tells a crash from a run still in progress.
    #: Exposed so a caller that suppressed the crash can stand in for the kernel.
    operation_lock: object | None = None

    def step(self, name: str, status: str, detail: str = "") -> None:
        self.steps.append((status, name, detail))


def candidate_is_documentation_space(address: str) -> bool:
    try:
        parsed = ipaddress.ip_address(address)
    except ValueError:
        return False
    return any(parsed in network for network in DOCUMENTATION_NETWORKS)


def run_phase_a(
    *,
    interface_index: int,
    interface_luid: int,
    interface_alias: str,
    interface_guid: str,
    candidate_address: str,
    target_prefix: str,
    prefix_length: int,
    environment_id: str | None,
    environment_authority_granted: bool,
    run_id: str,
    registry,
    journal,
    read_table,
    read_snapshot,
    create,
    delete,
    now: datetime,
    crash: bool = True,
    sleep=None,
    dad_timeout: float = 15.0,
) -> PhaseAResult:
    """Prove authority, create one row, record it durably, then die.

    ``crash`` exists so the whole sequence can be exercised in tests without
    killing the test runner. It defaults to True because a caller that forgot to
    ask for the crash would otherwise leave an address behind *and* return
    normally, which is the one combination nobody wants.
    """
    import time

    from .coexistence import capture_dhcp_baseline
    from .gate3 import evaluate_gate3_authority
    from .journal import (
        OwnedAddress,
        fingerprint_addresses,
        fingerprint_network_snapshot,
        fingerprint_row,
        new_operation_id,
        now_iso,
    )
    from .ownership_lock import LockUnavailable
    from .windows_unicast import NO_ERROR, describe_error

    sleep = sleep or time.sleep
    result = PhaseAResult(outcome="ENVIRONMENT_NOT_AUTHORISED")

    # --- 1. environment authority ------------------------------------------
    if not environment_authority_granted or not environment_id:
        result.step("environment-authority", "FAIL", "no disposable environment proven")
        result.evidence.append(
            "The crash experiment leaves an address behind on purpose, so it may "
            "only run on an adapter proven to belong to a disposable Recovery Lab "
            "environment."
        )
        result.restored = True
        return result
    result.step("environment-authority", "PASS", "disposable environment proven")

    # --- 2. the candidate must be unable to belong to a real network -------
    if not candidate_is_documentation_space(candidate_address):
        result.step("candidate", "FAIL", "outside RFC 5737 documentation space")
        result.outcome = "CANDIDATE_NOT_DOCUMENTATION_SPACE"
        result.evidence.append(
            "An address that outlives its process may only ever be one that "
            "cannot collide with a real host."
        )
        result.restored = True
        return result
    result.step("candidate", "PASS", "RFC 5737 documentation space")

    # --- 3. reservation authority, before any mutation ---------------------
    found, assessment = evaluate_gate3_authority(
        candidate_address=candidate_address,
        target_prefix=target_prefix,
        environment_id=environment_id,
        run_id=run_id,
        registry=registry,
        now=now,
        local_addresses=[row.address for row in read_table()],
        owned_addresses=[item.address for item in journal.outstanding()],
    )
    result.evidence.extend(assessment.evidence)
    if found is not None:
        result.reservation_id = found.reservation_id
    if not assessment.usable:
        result.step("reservation-authority", "FAIL", ", ".join(assessment.blockers))
        result.outcome = (
            "AUTHORITY_ABSENT"
            if "NO_RESERVATION" in assessment.blockers
            else "AUTHORITY_REFUSED"
        )
        result.restored = True
        return result
    result.step("reservation-authority", "PASS", candidate_address)

    # --- 4. baseline: prove the interface is what we think ------------------
    #
    # This is still read-only, so do it before consuming the one-shot
    # reservation. A refusal releases the selected reservation explicitly; it
    # is never silently left as authority for a later run.
    try:
        baseline_rows = read_table()
        baseline_snapshot = read_snapshot()
    except Exception as error:
        result.step("dhcp-baseline", "FAIL", f"read failed: {error}")
        result.outcome = "BASELINE_INCOMPLETE"
        result.restored = True
        _release_unclaimed_reservation(registry, found, now, result)
        return result
    if any(index == interface_index for index, _hop in baseline_snapshot.default_routes):
        result.step("default-route", "FAIL", "target interface carries a default route")
        result.outcome = "INTERFACE_CARRIES_DEFAULT_ROUTE"
        result.restored = True
        _release_unclaimed_reservation(registry, found, now, result)
        return result
    capture = capture_dhcp_baseline(
        interface_index=interface_index,
        interface_luid=interface_luid,
        temporary_address=candidate_address,
        rows=baseline_rows,
        snapshot=baseline_snapshot,
    )
    result.evidence.extend(capture.evidence)
    if capture.outcome != "CAPTURED" or capture.baseline is None:
        result.step("dhcp-baseline", "FAIL", capture.outcome)
        result.outcome = "BASELINE_INCOMPLETE"
        result.restored = True
        _release_unclaimed_reservation(registry, found, now, result)
        return result
    result.step("dhcp-baseline", "PASS", capture.baseline.primary.address)

    # --- 5. claim the operation with a lock the kernel will release --------
    operation_id = new_operation_id()
    result.operation_id = operation_id
    #
    # Taken before the durable intent so there is never a record on disk that
    # nobody is holding while its process is still alive.
    lock = journal.operation_lock(operation_id)
    try:
        lock.acquire()
    except Exception as error:
        held = isinstance(error, LockUnavailable)
        result.step("operation-lock", "FAIL", str(error))
        result.outcome = (
            "OPERATION_ALREADY_HELD" if held else "OPERATION_LOCK_FAILURE"
        )
        result.restored = True
        _release_unclaimed_reservation(registry, found, now, result)
        return result
    result.operation_lock = lock
    result.step("operation-lock", "PASS", "held until this process dies")

    # --- 6. atomically bind the reservation to this exact operation --------
    try:
        registry.claim(
            found.reservation_id,
            operation_id,
            expected_binding=found.operation_binding,
            now=now,
        )
    except Exception as error:
        result.step("reservation-binding", "FAIL", str(error))
        result.outcome = "RESERVATION_BINDING_FAILED"
        # A persistence error can be reported after the replace landed. Release
        # only if the registry now shows our exact binding; a CAS race won by a
        # different operation is deliberately untouched.
        _release_failed_claim_binding(
            registry, found.reservation_id, operation_id, now, result
        )
        lock.release()
        result.restored = True
        return result
    result.step("reservation-binding", "PASS", operation_id)

    # --- 7. durable intent, before the mutation ----------------------------
    owned = OwnedAddress(
        operation_id=operation_id,
        plan_id=run_id,
        interface_alias=interface_alias,
        interface_index=interface_index,
        interface_luid=interface_luid,
        address=candidate_address,
        prefix_length=prefix_length,
        created_at=now_iso(),
        state="INTENT_RECORDED",
        previous_state_fingerprint=fingerprint_addresses(
            [
                (row.address, row.prefix_length)
                for row in baseline_rows
                if row.interface_luid == interface_luid
            ]
        ),
        baseline_primary_address=capture.baseline.primary.address,
        baseline_primary_prefix_length=capture.baseline.primary.prefix_length,
        previous_network_fingerprint=fingerprint_network_snapshot(
            capture.baseline.snapshot
        ),
        environment_id=environment_id,
        interface_guid=interface_guid,
        reservation_id=found.reservation_id,
    )
    try:
        journal.record_intent(owned)
    except Exception as error:
        result.step("journal-intent", "FAIL", str(error))
        result.outcome = "JOURNAL_PERSISTENCE_FAILURE"
        result.evidence.append(
            "No create was attempted. The reservation is closed only if it is "
            "still bound to this operation."
        )
        reservation_result = _release_bound_reservation(
            registry, owned, now, result
        )
        try:
            if (
                reservation_result != "ERROR"
                and journal.get(operation_id) is not None
            ):
                journal.close(operation_id, "intent persistence reported failure")
                result.step("journal-close", "PASS", "no mutation was attempted")
        except Exception as close_error:
            result.step("journal-close", "WARN", str(close_error))
        lock.release()
        result.restored = True
        return result
    result.step("journal-intent", "PASS", operation_id)

    # --- 8. exactly one create ---------------------------------------------
    result.creates_attempted += 1
    try:
        code = create(
            address=candidate_address,
            prefix_length=prefix_length,
            interface_index=interface_index,
            interface_luid=interface_luid,
        )
    except Exception as error:
        code = None
        result.evidence.append(f"Create raised before returning a status: {error}.")
    if code != NO_ERROR:
        detail = describe_error(code) if isinstance(code, int) else "result unknown"
        result.step("create", "FAIL", detail)
        result.outcome = "ADDRESS_CREATE_FAILURE"
        _finish_without_delete_if_absent(
            result=result,
            owned=owned,
            baseline=capture.baseline,
            journal=journal,
            registry=registry,
            read_table=read_table,
            read_snapshot=read_snapshot,
            lock=lock,
            now=now,
            reason=f"create failed: {detail}",
        )
        return result
    result.step("create", "PASS", candidate_address)

    def _candidates():
        return [
            row
            for row in read_table()
            if row.address == candidate_address
            and row.interface_index == interface_index
            and row.interface_luid == interface_luid
        ]

    # --- 9. post-apply evidence, immediately -------------------------------
    #
    # Everything after this point is recoverable by a new process. Before it,
    # nothing is, so it happens first and nothing is allowed in between.
    candidates = _candidates()
    just_created = candidates[0] if len(candidates) == 1 else None
    if (
        just_created is None
        or just_created.creation_timestamp <= 0
        or just_created.prefix_length != prefix_length
        or (just_created.prefix_origin, just_created.suffix_origin)
        != ("MANUAL", "MANUAL")
    ):
        result.step("journal-created", "FAIL", "no post-apply evidence available")
        result.outcome = "POST_APPLY_EVIDENCE_UNAVAILABLE"
        result.evidence.append(
            "Exactly one row with the requested immutable properties and a "
            "positive creation timestamp could not be observed. Intent alone "
            "does not authorise a delete, so the process will not crash or "
            "guess at cleanup."
        )
        _finish_without_delete_if_absent(
            result=result,
            owned=owned,
            baseline=capture.baseline,
            journal=journal,
            registry=registry,
            read_table=read_table,
            read_snapshot=read_snapshot,
            lock=lock,
            now=now,
            reason="post-apply ownership evidence unavailable",
        )
        return result
    try:
        journal.record_created(
            operation_id,
            creation_timestamp=just_created.creation_timestamp,
            post_apply_fingerprint=fingerprint_row(just_created),
            note="crash experiment: evidence recorded before deliberate termination",
        )
    except Exception as error:
        result.step("journal-created", "FAIL", str(error))
        result.outcome = "JOURNAL_PERSISTENCE_FAILURE"
        result.evidence.append(
            "The row was observed in this process, but its post-apply identity "
            "was not proven durable. Normal exact rollback is attempted; a "
            "process death before that rollback finishes remains the unavoidable "
            "intent-only gap."
        )
        _rollback_before_crash(
            result=result,
            owned=owned,
            expected_row=just_created,
            baseline=capture.baseline,
            journal=journal,
            registry=registry,
            read_table=read_table,
            read_snapshot=read_snapshot,
            delete=delete,
            lock=lock,
            now=now,
            reason="post-apply journal persistence failed",
        )
        return result
    result.step("journal-created", "PASS", "post-apply evidence durable")

    # --- 10. settle DAD so the outstanding row is unambiguous --------------
    waited = 0.0
    observed = just_created
    while waited <= dad_timeout:
        observed = next(
            (
                row
                for row in _candidates()
                if row.creation_timestamp == just_created.creation_timestamp
                and fingerprint_row(row) == fingerprint_row(just_created)
            ),
            None,
        )
        state = observed.dad_state if observed else "ABSENT"
        if state not in ("TENTATIVE", "ABSENT"):
            break
        sleep(0.5)
        waited += 0.5
    result.dad_state = observed.dad_state if observed else "ABSENT"
    if result.dad_state != "PREFERRED":
        # Do not crash on top of an unsettled row: the operator would be left
        # unable to tell an unowned address from an unfinished one.
        result.step("dad", "FAIL", f"{result.dad_state} after {waited:.1f}s")
        result.outcome = "DAD_NOT_PREFERRED"
        result.evidence.append(
            "The row did not reach Preferred, so the process will not crash "
            "deliberately. The still-live process attempts exact rollback; if "
            "that cannot be proven complete, durable ownership remains open."
        )
        _rollback_before_crash(
            result=result,
            owned=owned,
            expected_row=just_created,
            baseline=capture.baseline,
            journal=journal,
            registry=registry,
            read_table=read_table,
            read_snapshot=read_snapshot,
            delete=delete,
            lock=lock,
            now=now,
            reason=f"DAD did not reach Preferred ({result.dad_state})",
        )
        return result
    result.step("dad", "PASS", f"preferred after {waited:.1f}s")

    # --- 11. die -----------------------------------------------------------
    result.outcome = "CRASHED_AS_INTENDED"
    if not crash:
        # Test path. The address is still outstanding and the caller owns it.
        result.step("crash", "SKIPPED", "crash disabled by caller")
        return result

    result.step("crash", "PASS", f"terminating with exit code {CRASH_EXIT_CODE}")
    _report(result)
    _hard_exit()
    raise AssertionError("unreachable: the process should be gone")  # pragma: no cover


def _release_unclaimed_reservation(registry, reservation, now, result) -> None:
    """Consume a selected reservation only if nobody claimed it meanwhile."""
    try:
        released = registry.release(
            reservation.reservation_id,
            now=now,
            expected_binding=reservation.operation_binding,
        )
    except Exception as error:
        result.step("reservation-release", "WARN", str(error))
        return
    result.step(
        "reservation-release",
        "PASS" if released else "WARN",
        "unused reservation closed" if released else "binding changed; not released",
    )


def _release_failed_claim_binding(
    registry, reservation_id, operation_id, now, result
) -> None:
    """Undo only a binding that a failed claim may have persisted."""
    try:
        released = registry.release(
            reservation_id,
            now=now,
            expected_binding=operation_id,
        )
    except Exception as error:
        result.step("reservation-release", "WARN", str(error))
        return
    result.step(
        "reservation-release",
        "PASS" if released else "WARN",
        (
            "failed claim binding closed"
            if released
            else "no binding to this failed operation was released"
        ),
    )


def _release_bound_reservation(registry, owned, now, result) -> str:
    """Release only the reservation still bound to `owned.operation_id`."""
    try:
        released = registry.release(
            owned.reservation_id,
            now=now,
            expected_binding=owned.operation_id,
        )
    except Exception as error:
        result.step("reservation-release", "FAIL", str(error))
        return "ERROR"
    result.step(
        "reservation-release",
        "PASS" if released else "WARN",
        "operation reservation closed" if released else "not bound to this operation",
    )
    return "RELEASED" if released else "NOT_RELEASED"


def _baseline_primary_survived(rows, baseline) -> bool:
    return any(
        row.address == baseline.primary.address
        and row.interface_index == baseline.interface_index
        and row.interface_luid == baseline.interface_luid
        and row.is_dhcp
        and row.is_usable
        and row.has_finite_lease
        for row in rows
    )


def _finish_without_delete_if_absent(
    *,
    result,
    owned,
    baseline,
    journal,
    registry,
    read_table,
    read_snapshot,
    lock,
    now,
    reason,
) -> None:
    """Close a failed Phase A only when the delete-key row is already absent."""
    try:
        rows = read_table()
    except Exception as error:
        result.step("verify-absent", "FAIL", f"table read failed: {error}")
        lock.release()
        return
    present = any(
        row.address == owned.address and row.interface_luid == owned.interface_luid
        for row in rows
    )
    if present:
        result.step(
            "verify-absent",
            "FAIL",
            "an address with the API deletion key remains; durable state retained",
        )
        result.evidence.append(
            "No delete was issued because durable post-apply identity is absent. "
            "The bound reservation and journal intent remain for manual "
            "reconciliation; they cannot authorise a new run."
        )
        lock.release()
        return
    result.step("verify-absent", "PASS", "no address with the deletion key remains")
    if not _baseline_primary_survived(rows, baseline):
        result.step("dhcp-primary", "FAIL", "DHCP primary was not re-proven")
        lock.release()
        return
    result.step("dhcp-primary", "PASS", "still DHCP/DHCP, Preferred, finite lease")
    from .journal import fingerprint_addresses, fingerprint_network_snapshot

    on_interface = [
        row
        for row in rows
        if row.interface_index == owned.interface_index
        and row.interface_luid == owned.interface_luid
    ]
    if (
        fingerprint_addresses(
            [(row.address, row.prefix_length) for row in on_interface]
        )
        != owned.previous_state_fingerprint
    ):
        result.step("baseline-restored", "FAIL", "addressing differs")
        lock.release()
        return
    try:
        current_snapshot = read_snapshot()
    except Exception as error:
        result.step("network-baseline", "FAIL", f"read failed: {error}")
        lock.release()
        return
    if (
        fingerprint_network_snapshot(current_snapshot)
        != owned.previous_network_fingerprint
    ):
        result.step("network-baseline", "FAIL", "network state differs")
        lock.release()
        return
    result.step("baseline-restored", "PASS", "pre-operation addressing")
    result.step("network-baseline", "PASS", "routes/DNS/source selection")
    result.restored = True
    reservation_result = _release_bound_reservation(
        registry, owned, now, result
    )
    if reservation_result == "ERROR":
        result.evidence.append(
            "The address is absent, but reservation persistence failed. The "
            "journal remains open so a later reconciler retains the operation "
            "to reservation linkage."
        )
        lock.release()
        return
    try:
        journal.close(owned.operation_id, reason)
    except Exception as error:
        result.step("journal-close", "FAIL", str(error))
    else:
        result.step("journal-close", "PASS", owned.operation_id)
    lock.release()


def _rollback_before_crash(
    *,
    result,
    owned,
    expected_row,
    baseline,
    journal,
    registry,
    read_table,
    read_snapshot,
    delete,
    lock,
    now,
    reason,
) -> None:
    """Normal same-process rollback after create, never crash reconciliation."""
    from .journal import fingerprint_row
    from .windows_unicast import NO_ERROR, describe_error

    try:
        rows = read_table()
    except Exception as error:
        result.step("rollback-authority", "FAIL", f"table read failed: {error}")
        lock.release()
        return
    keyed = [
        row
        for row in rows
        if row.address == owned.address and row.interface_luid == owned.interface_luid
    ]
    if not keyed:
        _finish_without_delete_if_absent(
            result=result,
            owned=owned,
            baseline=baseline,
            journal=journal,
            registry=registry,
            read_table=read_table,
            read_snapshot=read_snapshot,
            lock=lock,
            now=now,
            reason=reason + "; row already absent",
        )
        return
    exact = [
        row
        for row in keyed
        if row.interface_index == owned.interface_index
        and row.prefix_length == owned.prefix_length
        and row.creation_timestamp == expected_row.creation_timestamp
        and fingerprint_row(row) == fingerprint_row(expected_row)
        and (row.prefix_origin, row.suffix_origin) == ("MANUAL", "MANUAL")
    ]
    if len(keyed) != 1 or len(exact) != 1:
        result.step(
            "rollback-authority",
            "FAIL",
            "live row no longer matches the row observed after create",
        )
        result.evidence.append(
            "Normal rollback also fails closed if the live deletion-key row "
            "changed before deletion. Durable state and the reservation remain."
        )
        lock.release()
        return
    result.step("rollback-authority", "PASS", "same observed row re-proven")
    result.deletes_attempted += 1
    code = delete(
        address=owned.address,
        prefix_length=owned.prefix_length,
        interface_index=owned.interface_index,
        interface_luid=owned.interface_luid,
    )
    result.step(
        "delete",
        "PASS" if code == NO_ERROR else "FAIL",
        describe_error(code),
    )
    _finish_without_delete_if_absent(
        result=result,
        owned=owned,
        baseline=baseline,
        journal=journal,
        registry=registry,
        read_table=read_table,
        read_snapshot=read_snapshot,
        lock=lock,
        now=now,
        reason=reason,
    )


def _report(result: PhaseAResult) -> None:
    """Say how far we got, then stop. Reporting is not cleanup."""
    print(f"outcome    : {result.outcome}")
    print(f"operation  : {result.operation_id}")
    print(f"reservation: {result.reservation_id}")
    print(f"creates    : {result.creates_attempted}")
    print(f"dad        : {result.dad_state}")
    print("steps:")
    for status, name, detail in result.steps:
        print(f"  {status:<8} {name:<22} {detail}")
    print()
    print("The temporary address is still present and the journal entry is open.")
    print("This process is about to terminate without cleaning up, on purpose.")
    sys.stdout.flush()
    sys.stderr.flush()


def _hard_exit() -> None:
    """Terminate now, skipping every Python-level cleanup path.

    ``os._exit`` bypasses ``finally`` blocks, ``atexit`` handlers, context
    manager exits, destructors and buffer flushing. The address stays, the
    journal entry stays open, the reservation stays bound, and the kernel
    releases the operation lock -- which is precisely the state a new process
    has to recover from.
    """
    os._exit(CRASH_EXIT_CODE)
