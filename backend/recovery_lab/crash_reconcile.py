"""Reconstructing ownership after the process that created an address died.

The question is narrow and the wrong answer is expensive: *may this new process
delete this row?* Nothing here searches for temporary-looking addresses, matches
on subnet, or cleans documentation space. There is exactly one shape of
permitted action -- delete the one row a durable record proves we created -- and
every other outcome is a refusal.

The verdict is a conjunction. `adjudicate` walks a fixed list of predicates and
`DELETE_AUTHORISED` is reachable only when all of them hold. Changing any
component of the recorded identity makes it unreachable, which is the property
the adversarial tests exercise directly.

Three authorities stay separate here, as they have since Gate 3:

* **Environment authority** -- may we touch this interface at all? Re-proven
  live through the VirtualBox GUID chain, never read back from the record.
* **Reservation authority** -- were we authorised to *create* this address?
  Answered at creation time and deliberately *not* required now: deleting a row
  we own needs no reservation, and requiring one would make an expired
  reservation block cleanup of an address that is already outstanding.
* **Row ownership** -- is this exact live row ours to remove? That is what this
  module decides, and it is the only one that authorises the delete.

Scope: process death on the same Windows boot, same adapter object. A reboot,
NIC reset, driver restart or adapter recreation is a different, unmeasured
transition. The code may be able to prove simple absence afterwards, but it does
not infer which event caused absence or claim those transitions are validated.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal, Sequence

from .environment import normalise_guid
from .journal import OwnedAddress, fingerprint_row

Verdict = Literal[
    #: The only outcome that permits a mutation, and only of one exact row.
    "DELETE_AUTHORISED",
    #: The recorded row is not there. Nothing to delete; the record may close.
    "ALREADY_ABSENT",
    #: A row is there but the record cannot prove it is the one we created.
    "OWNERSHIP_UNPROVABLE",
    #: Something on the machine actively disagrees with the record.
    "CONTRADICTED",
    #: The interface is not one we may touch, or is no longer identifiable.
    "ENVIRONMENT_NOT_AUTHORISED",
    #: Another process still holds this operation. Not ours to reconcile.
    "OWNER_PROCESS_ALIVE",
    #: More than one record claims the same row.
    "AMBIGUOUS_CLAIM",
    #: The record is already finished.
    "ALREADY_CLOSED",
]

#: Every reason a reconciliation refused. Named so refusals can be asserted on
#: rather than inferred from a message.
RefusalCode = Literal[
    "JOURNAL_ABSENT",
    "RECORD_ALREADY_CLOSED",
    "OWNER_PROCESS_STILL_RUNNING",
    "ENVIRONMENT_RECORD_ABSENT",
    "ENVIRONMENT_AUTHORITY_REFUSED",
    "ENVIRONMENT_MISMATCH",
    "INTERFACE_GUID_MISMATCH",
    "INTERFACE_GUID_UNRESOLVED",
    "INTERFACE_AMBIGUOUS",
    "LIVE_INTERFACE_IDENTITY_UNRESOLVED",
    "LIVE_INTERFACE_INDEX_MISMATCH",
    "LIVE_INTERFACE_LUID_MISMATCH",
    "BASELINE_EVIDENCE_ABSENT",
    "NO_POST_APPLY_EVIDENCE",
    "ROW_FINGERPRINT_MISMATCH",
    "ROW_CREATION_TIMESTAMP_MISMATCH",
    "ROW_PREFIX_MISMATCH",
    "ROW_ORIGIN_CONTRADICTS_OWNERSHIP",
    "DUPLICATE_CLAIMANT",
    "ADDRESS_PRESENT_ON_ANOTHER_INTERFACE",
    "MULTIPLE_MATCHING_ROWS",
    "OBSERVATION_FAILED",
]


@dataclass
class Adjudication:
    verdict: Verdict
    refusals: list[RefusalCode] = field(default_factory=list)
    evidence: list[str] = field(default_factory=list)

    @property
    def may_delete(self) -> bool:
        return self.verdict == "DELETE_AUTHORISED"


def adjudicate(
    record: OwnedAddress,
    *,
    rows: Sequence,
    all_records: Sequence[OwnedAddress],
    environment_authority_granted: bool,
    environment_id: str | None,
    live_interface_guid: str | None,
    live_interface_index: int | None,
    live_interface_luid: int | None,
    owner_process_gone: bool,
) -> Adjudication:
    """Decide what, if anything, may be done about one outstanding record.

    Ordered from "we should not be here at all" to "this exact row is ours", so
    that a refusal names the most fundamental problem rather than whichever
    check happened to run first.
    """
    refusals: list[RefusalCode] = []
    evidence: list[str] = []

    # --- 1. is this record even a live claim? ------------------------------
    if record.state == "COMPLETED":
        return Adjudication(
            verdict="ALREADY_CLOSED",
            refusals=["RECORD_ALREADY_CLOSED"],
            evidence=["The record is already closed and claims nothing."],
        )

    # --- 2. is anybody still using it? -------------------------------------
    if not owner_process_gone:
        return Adjudication(
            verdict="OWNER_PROCESS_ALIVE",
            refusals=["OWNER_PROCESS_STILL_RUNNING"],
            evidence=[
                "A live process still holds this operation. Reconciliation is "
                "for crashes, not for taking work away from a running run."
            ],
        )

    # --- 3. environment authority (question one, re-proven live) -----------
    if not environment_authority_granted or not environment_id:
        return Adjudication(
            verdict="ENVIRONMENT_NOT_AUTHORISED",
            refusals=["ENVIRONMENT_AUTHORITY_REFUSED"],
            evidence=[
                "The interface is not proven to belong to a disposable "
                "Recovery Lab environment, so nothing on it may be touched."
            ],
        )
    if record.environment_id and record.environment_id != environment_id:
        return Adjudication(
            verdict="ENVIRONMENT_NOT_AUTHORISED",
            refusals=["ENVIRONMENT_MISMATCH"],
            evidence=[
                "The record was written for a different disposable environment "
                "than the one this interface belongs to now."
            ],
        )
    if not record.environment_id:
        return Adjudication(
            verdict="OWNERSHIP_UNPROVABLE",
            refusals=["ENVIRONMENT_RECORD_ABSENT"],
            evidence=[
                "The record does not name the environment it was created in, so "
                "environment ownership cannot be re-proven for it."
            ],
        )

    # --- 4. durable Windows identity ---------------------------------------
    recorded_guid = normalise_guid(record.interface_guid)
    observed_guid = normalise_guid(live_interface_guid)
    if recorded_guid is None or observed_guid is None:
        return Adjudication(
            verdict="ENVIRONMENT_NOT_AUTHORISED",
            refusals=["INTERFACE_GUID_UNRESOLVED"],
            evidence=[
                "The adapter's durable identity could not be resolved on both "
                "sides, and an alias or index is not identity."
            ],
        )
    if recorded_guid != observed_guid:
        return Adjudication(
            verdict="CONTRADICTED",
            refusals=["INTERFACE_GUID_MISMATCH"],
            evidence=[
                "The adapter now carrying this environment is not the adapter "
                "the record was written against."
            ],
        )
    evidence.append("Adapter identity re-proven against the recorded GUID.")

    # GUID says which adapter object VirtualBox and Windows agree on. The live
    # LUID/index say whether the address-table identity recorded by Phase A is
    # still that adapter now. This matters especially for ALREADY_ABSENT: an
    # absent row may be closed only after the exact interface identity has been
    # re-proven, not merely because an old index has no matching address.
    if (
        live_interface_index is None
        or live_interface_index <= 0
        or live_interface_luid is None
        or live_interface_luid <= 0
    ):
        return Adjudication(
            verdict="ENVIRONMENT_NOT_AUTHORISED",
            refusals=["LIVE_INTERFACE_IDENTITY_UNRESOLVED"],
            evidence=[
                "The current adapter's IP Helper LUID/index could not both be "
                "resolved, so row absence cannot be interpreted safely."
            ],
        )
    if live_interface_luid != record.interface_luid:
        return Adjudication(
            verdict="CONTRADICTED",
            refusals=["LIVE_INTERFACE_LUID_MISMATCH"],
            evidence=[
                "The GUID-correlated adapter now has a different interface LUID "
                "than the operation recorded."
            ],
        )
    if live_interface_index != record.interface_index:
        return Adjudication(
            verdict="CONTRADICTED",
            refusals=["LIVE_INTERFACE_INDEX_MISMATCH"],
            evidence=[
                "The GUID-correlated adapter now has a different interface index "
                "than the operation recorded."
            ],
        )
    evidence.append("Live interface LUID and index still match the record.")

    if (
        not record.previous_state_fingerprint
        or not record.baseline_primary_address
        or record.baseline_primary_prefix_length <= 0
        or not record.previous_network_fingerprint
    ):
        return Adjudication(
            verdict="OWNERSHIP_UNPROVABLE",
            refusals=["BASELINE_EVIDENCE_ABSENT"],
            evidence=[
                "The operation lacks the durable pre-mutation evidence needed "
                "to prove restoration in a new process. Refusing to delete."
            ],
        )

    # --- 5. is anybody else claiming the same row? -------------------------
    claimants = [
        other
        for other in all_records
        if other.operation_id != record.operation_id
        and other.state != "COMPLETED"
        and (
            other.interface_luid,
            other.interface_index,
            other.address,
            other.prefix_length,
        )
        == (
            record.interface_luid,
            record.interface_index,
            record.address,
            record.prefix_length,
        )
    ]
    if claimants:
        return Adjudication(
            verdict="AMBIGUOUS_CLAIM",
            refusals=["DUPLICATE_CLAIMANT"],
            evidence=[
                f"{len(claimants) + 1} outstanding records claim the same row. "
                "Ownership must be resolved by a human, not chosen."
            ],
        )

    # --- 6. what is actually on the machine? -------------------------------
    exact = [
        row
        for row in rows
        if row.address == record.address
        and row.interface_luid == record.interface_luid
        and row.interface_index == record.interface_index
        and row.prefix_length == record.prefix_length
    ]
    elsewhere = [
        row
        for row in rows
        if row.address == record.address
        and (
            row.interface_luid != record.interface_luid
            or row.interface_index != record.interface_index
        )
    ]
    wrong_prefix = [
        row
        for row in rows
        if row.address == record.address
        and row.interface_luid == record.interface_luid
        and row.interface_index == record.interface_index
        and row.prefix_length != record.prefix_length
    ]

    if len(exact) > 1:
        # Windows should not permit this; if it happened, do not guess.
        return Adjudication(
            verdict="AMBIGUOUS_CLAIM",
            refusals=["MULTIPLE_MATCHING_ROWS"],
            evidence=["More than one live row matches the recorded identity."],
        )

    if not exact:
        if wrong_prefix:
            return Adjudication(
                verdict="CONTRADICTED",
                refusals=["ROW_PREFIX_MISMATCH"],
                evidence=[
                    "The recorded address exists on the recorded interface but "
                    "with a different prefix. It is not treated as absence and "
                    "will not be deleted."
                ],
            )
        if elsewhere:
            # The same address exists somewhere else. It is emphatically not
            # ours, and its presence must not turn into a reason to go looking.
            evidence.append(
                "The same address exists on a different interface. It is not "
                "the recorded row and will not be touched."
            )
            refusals.append("ADDRESS_PRESENT_ON_ANOTHER_INTERFACE")
        evidence.append(
            "The recorded row is absent from the interface it was created on."
        )
        # Absence on a re-proven interface is provable, and proving a row is
        # gone is enough to stop claiming it. It is *not* evidence about why it
        # went, and nothing is deleted on this path.
        return Adjudication(
            verdict="ALREADY_ABSENT", refusals=refusals, evidence=evidence
        )

    live = exact[0]

    # --- 7. post-apply evidence: is this the object we created? ------------
    if not record.has_post_apply_evidence:
        return Adjudication(
            verdict="OWNERSHIP_UNPROVABLE",
            refusals=["NO_POST_APPLY_EVIDENCE"],
            evidence=[
                "A row matching the recorded description is present, but the "
                "record never captured what the created row was. It may be ours "
                "or it may be somebody else's identical address, and there is no "
                "way to tell. Refusing to delete."
            ],
        )
    if live.creation_timestamp != record.creation_timestamp:
        return Adjudication(
            verdict="CONTRADICTED",
            refusals=["ROW_CREATION_TIMESTAMP_MISMATCH"],
            evidence=[
                "The address is present but the operating system created that "
                "row at a different moment than the one we recorded. This is a "
                "different object that happens to have the same address."
            ],
        )
    if fingerprint_row(live) != record.post_apply_fingerprint:
        return Adjudication(
            verdict="CONTRADICTED",
            refusals=["ROW_FINGERPRINT_MISMATCH"],
            evidence=[
                "The row's recorded properties no longer match what was observed "
                "immediately after it was created."
            ],
        )
    if (live.prefix_origin, live.suffix_origin) != ("MANUAL", "MANUAL"):
        return Adjudication(
            verdict="CONTRADICTED",
            refusals=["ROW_ORIGIN_CONTRADICTS_OWNERSHIP"],
            evidence=[
                f"The row reports {live.prefix_origin}/{live.suffix_origin}. The "
                "harness only ever creates MANUAL/MANUAL rows, so this is not "
                "one of ours."
            ],
        )

    evidence.append(
        f"{record.address}/{record.prefix_length} matches the recorded row "
        "under every required predicate, including the creation timestamp "
        "Windows reported after apply."
    )
    return Adjudication(verdict="DELETE_AUTHORISED", evidence=evidence)


# --- acting on the verdict --------------------------------------------------
#
# Separated from `adjudicate` on purpose. The decision is a pure function of
# durable evidence plus observed state, so the adversarial tests can enumerate
# it exhaustively without a Windows API in sight; this half only carries it out.

ReconcileOutcome = Literal[
    "NOTHING_OUTSTANDING",
    "RECONCILED",
    "ALREADY_ABSENT",
    "BLOCKED",
    "PARTIAL",
]


@dataclass
class RecordOutcome:
    operation_id: str
    verdict: Verdict
    deleted: bool = False
    closed: bool = False
    reservation_released: bool = False
    refusals: list[RefusalCode] = field(default_factory=list)
    evidence: list[str] = field(default_factory=list)


@dataclass
class ReconcileResult:
    outcome: ReconcileOutcome = "NOTHING_OUTSTANDING"
    records: list[RecordOutcome] = field(default_factory=list)
    steps: list[tuple[str, str, str]] = field(default_factory=list)
    #: Deletes actually issued. Every refusal path must leave this at 0.
    deletes_attempted: int = 0
    outstanding_after: int = -1

    def step(self, name: str, status: str, detail: str = "") -> None:
        self.steps.append((status, name, detail))


def reconcile_after_crash(
    *,
    journal,
    reservations,
    read_table,
    read_snapshot,
    delete,
    environment_authority_granted: bool,
    environment_id: str | None,
    live_interface_guid: str | None,
    live_interface_index: int | None,
    live_interface_luid: int | None,
    now,
) -> ReconcileResult:
    """Recover from durable state, deleting only what can be proven ours.

    The table is re-read for each record so a delete is decided against the same
    observation it is carried out on, and read again afterwards to prove the row
    is gone rather than trusting the API to have told the truth.
    """
    from .ownership_lock import LockUnavailable, exclusive

    result = ReconcileResult()
    try:
        # This lock is separate from the short journal mutation lock. It spans
        # observation, adjudication, the possible delete, verification and
        # closure, so two Phase B processes cannot both acquire deletion
        # authority from the same pre-delete snapshot.
        with exclusive(journal.reconciliation_lock_path, blocking=False):
            return _reconcile_claimed(
                result=result,
                journal=journal,
                reservations=reservations,
                read_table=read_table,
                read_snapshot=read_snapshot,
                delete=delete,
                environment_authority_granted=environment_authority_granted,
                environment_id=environment_id,
                live_interface_guid=live_interface_guid,
                live_interface_index=live_interface_index,
                live_interface_luid=live_interface_luid,
                now=now,
            )
    except LockUnavailable:
        result.outcome = "BLOCKED"
        result.step(
            "reconciliation-lock",
            "FAIL",
            "another reconciliation process is already active",
        )
        result.outstanding_after = len(journal.outstanding())
        return result


def _reconcile_claimed(
    *,
    result,
    journal,
    reservations,
    read_table,
    read_snapshot,
    delete,
    environment_authority_granted,
    environment_id,
    live_interface_guid,
    live_interface_index,
    live_interface_luid,
    now,
) -> ReconcileResult:
    from .journal import fingerprint_addresses
    from .ownership_lock import LockUnavailable
    from .windows_unicast import NO_ERROR, describe_error

    outstanding = journal.outstanding()
    if not outstanding:
        result.step("journal", "PASS", "no outstanding owned rows")
        result.outstanding_after = 0
        return result
    result.step("journal", "PASS", f"{len(outstanding)} outstanding record(s)")

    for snapshot in outstanding:
        operation_lock = journal.operation_lock(snapshot.operation_id)
        try:
            # Unlike the old liveness probe, this claim stays held until the
            # record is either safely closed or deliberately left outstanding.
            operation_lock.acquire()
        except LockUnavailable:
            item = RecordOutcome(
                operation_id=snapshot.operation_id,
                verdict="OWNER_PROCESS_ALIVE",
                refusals=["OWNER_PROCESS_STILL_RUNNING"],
                evidence=[
                    "The operation lock is held by the original process or "
                    "another live claimant. No observation or delete followed."
                ],
            )
            result.records.append(item)
            result.step(
                "operation-lock", "FAIL", f"{snapshot.operation_id}: still held"
            )
            continue

        try:
            # The initial list is only work discovery. Authority is always based
            # on a fresh record and a fresh all-records snapshot taken after this
            # process owns the operation lock.
            record = journal.get(snapshot.operation_id)
            if record is None or record.state == "COMPLETED":
                item = RecordOutcome(
                    operation_id=snapshot.operation_id,
                    verdict="ALREADY_CLOSED",
                    refusals=["RECORD_ALREADY_CLOSED"],
                    evidence=["The record closed before this process claimed it."],
                )
                result.records.append(item)
                result.step("adjudicate", "FAIL", "record already closed")
                continue

            verdict = _adjudicate_fresh(
                record=record,
                rows=read_table(),
                all_records=journal.all(),
                environment_authority_granted=environment_authority_granted,
                environment_id=environment_id,
                live_interface_guid=live_interface_guid,
                live_interface_index=live_interface_index,
                live_interface_luid=live_interface_luid,
            )
            item = RecordOutcome(
                operation_id=record.operation_id,
                verdict=verdict.verdict,
                refusals=list(verdict.refusals),
                evidence=list(verdict.evidence),
            )
            result.records.append(item)

            if verdict.verdict not in ("DELETE_AUTHORISED", "ALREADY_ABSENT"):
                result.step(
                    "adjudicate", "FAIL", f"{record.operation_id}: {verdict.verdict}"
                )
                continue

            if verdict.verdict == "DELETE_AUTHORISED":
                # Re-read immediately before calling an API whose deletion key
                # is address + interface. This is not an atomic compare/delete,
                # but it refuses any contradiction observable before mutation.
                confirmation = _adjudicate_fresh(
                    record=record,
                    rows=read_table(),
                    all_records=journal.all(),
                    environment_authority_granted=environment_authority_granted,
                    environment_id=environment_id,
                    live_interface_guid=live_interface_guid,
                    live_interface_index=live_interface_index,
                    live_interface_luid=live_interface_luid,
                )
                if confirmation.verdict != "DELETE_AUTHORISED":
                    _replace_verdict(item, confirmation)
                    result.step(
                        "pre-delete-recheck",
                        "FAIL",
                        f"identity changed: {confirmation.verdict}",
                    )
                    continue
                result.step(
                    "adjudicate",
                    "PASS",
                    f"{record.operation_id}: exact observed row proven ours",
                )
                result.deletes_attempted += 1
                code = delete(
                    address=record.address,
                    prefix_length=record.prefix_length,
                    interface_index=record.interface_index,
                    interface_luid=record.interface_luid,
                )
                if code != NO_ERROR:
                    item.evidence.append(f"Delete failed: {describe_error(code)}.")
                    result.step("delete", "FAIL", describe_error(code))
                    continue
                result.step(
                    "delete", "PASS", f"{record.address}/{record.prefix_length}"
                )
                after = read_table()
                absence = _adjudicate_fresh(
                    record=record,
                    rows=after,
                    all_records=journal.all(),
                    environment_authority_granted=environment_authority_granted,
                    environment_id=environment_id,
                    live_interface_guid=live_interface_guid,
                    live_interface_index=live_interface_index,
                    live_interface_luid=live_interface_luid,
                )
                if absence.verdict != "ALREADY_ABSENT":
                    item.evidence.extend(absence.evidence)
                    item.refusals.extend(absence.refusals)
                    result.step(
                        "verify-absent",
                        "FAIL",
                        f"post-delete verdict {absence.verdict}",
                    )
                    continue
                item.deleted = True
                result.step("verify-absent", "PASS", "recorded row confirmed absent")
            else:
                result.step(
                    "adjudicate", "PASS", f"{record.operation_id}: already absent"
                )
                after = read_table()
                absence = _adjudicate_fresh(
                    record=record,
                    rows=after,
                    all_records=journal.all(),
                    environment_authority_granted=environment_authority_granted,
                    environment_id=environment_id,
                    live_interface_guid=live_interface_guid,
                    live_interface_index=live_interface_index,
                    live_interface_luid=live_interface_luid,
                )
                if absence.verdict != "ALREADY_ABSENT":
                    _replace_verdict(item, absence)
                    result.step(
                        "verify-absent",
                        "FAIL",
                        f"absence no longer holds: {absence.verdict}",
                    )
                    continue
                result.step("verify-absent", "PASS", "absence confirmed again")

            on_interface = [
                row
                for row in after
                if row.interface_luid == record.interface_luid
                and row.interface_index == record.interface_index
            ]
            primary_matches = [
                row
                for row in on_interface
                if row.address == record.baseline_primary_address
                and row.prefix_length == record.baseline_primary_prefix_length
                and row.is_dhcp
                and row.is_usable
                and row.has_finite_lease
            ]
            if len(primary_matches) != 1:
                item.evidence.append(
                    "The exact pre-operation DHCP primary was not re-proven as "
                    "DHCP/DHCP, Preferred, and finite-lease, so the record stays "
                    "open for a human to inspect."
                )
                result.step("dhcp-primary", "FAIL", "exact DHCP primary not preserved")
                continue
            result.step(
                "dhcp-primary",
                "PASS",
                "same address/prefix, DHCP/DHCP, Preferred, finite lease",
            )

            restored = fingerprint_addresses(
                [(row.address, row.prefix_length) for row in on_interface]
            )
            if restored != record.previous_state_fingerprint:
                item.evidence.append(
                    "Interface addressing differs from the pre-operation "
                    "fingerprint. The difference was not touched and the "
                    "ownership record remains open."
                )
                result.step(
                    "baseline-restored", "FAIL", "addressing differs elsewhere"
                )
                continue
            result.step("baseline-restored", "PASS", "pre-operation addressing")

            try:
                current_snapshot = read_snapshot()
            except Exception as error:
                item.evidence.append(f"Network baseline could not be read: {error}.")
                result.step("network-baseline", "FAIL", str(error))
                continue
            from .journal import fingerprint_network_snapshot

            if (
                fingerprint_network_snapshot(current_snapshot)
                != record.previous_network_fingerprint
            ):
                item.evidence.append(
                    "Routes, default routes, DNS, or measured source selection "
                    "differ from the pre-operation snapshot. No unrelated state "
                    "was modified and the ownership record remains open."
                )
                result.step("network-baseline", "FAIL", "network state differs")
                continue
            result.step("network-baseline", "PASS", "pre-operation network state")

            # Release bookkeeping before closing the ownership claim. If the
            # process dies between these writes, a rerun sees an outstanding but
            # absent row and can safely finish. The opposite order strands a
            # bound reservation with no outstanding record linking it back.
            if record.reservation_id:
                try:
                    reservation_state = _release_own_reservation(
                        reservations,
                        record.reservation_id,
                        record.operation_id,
                        now,
                    )
                except Exception as error:
                    item.evidence.append(
                        f"Reservation lifecycle could not be persisted: {error}."
                    )
                    result.step("reservation-release", "FAIL", str(error))
                    continue
                item.reservation_released = reservation_state == "RELEASED"
                result.step(
                    "reservation-release",
                    "PASS" if reservation_state in ("RELEASED", "ALREADY_RELEASED") else "WARN",
                    reservation_state,
                )

            try:
                journal.close(
                    record.operation_id,
                    "reconciled after crash" if item.deleted else "row already absent",
                )
            except Exception as error:
                item.evidence.append(
                    f"Journal closure could not be confirmed durable: {error}."
                )
                result.step("journal-close", "FAIL", str(error))
                continue
            item.closed = True
            result.step("journal-close", "PASS", record.operation_id)
        except Exception as error:
            existing = next(
                (
                    item
                    for item in result.records
                    if item.operation_id == snapshot.operation_id
                ),
                None,
            )
            if existing is None:
                existing = RecordOutcome(
                    operation_id=snapshot.operation_id,
                    verdict="OWNERSHIP_UNPROVABLE",
                    refusals=["OBSERVATION_FAILED"],
                )
                result.records.append(existing)
            elif "OBSERVATION_FAILED" not in existing.refusals:
                existing.refusals.append("OBSERVATION_FAILED")
            existing.evidence.append(
                f"A required observation or operation failed: {error}. The "
                "record remains open."
            )
            result.step("reconcile-record", "FAIL", str(error))
        finally:
            operation_lock.release()

    result.outstanding_after = len(journal.outstanding())
    closed = [item for item in result.records if item.closed]
    blocked = [item for item in result.records if not item.closed]
    deleted = [item for item in result.records if item.deleted]
    if blocked and closed:
        result.outcome = "PARTIAL"
    elif blocked:
        result.outcome = "BLOCKED"
    elif deleted:
        result.outcome = "RECONCILED"
    else:
        result.outcome = "ALREADY_ABSENT"
    return result


def _adjudicate_fresh(
    *,
    record,
    rows,
    all_records,
    environment_authority_granted,
    environment_id,
    live_interface_guid,
    live_interface_index,
    live_interface_luid,
):
    return adjudicate(
        record,
        rows=rows,
        all_records=all_records,
        environment_authority_granted=environment_authority_granted,
        environment_id=environment_id,
        live_interface_guid=live_interface_guid,
        live_interface_index=live_interface_index,
        live_interface_luid=live_interface_luid,
        owner_process_gone=True,
    )


def _replace_verdict(item: RecordOutcome, verdict: Adjudication) -> None:
    item.verdict = verdict.verdict
    item.refusals = list(verdict.refusals)
    item.evidence.extend(verdict.evidence)


def _release_own_reservation(
    reservations, reservation_id, operation_id, now
) -> str:
    """Close only this operation's binding; never rebind or infer authority."""
    matches = [
        item
        for item in reservations.all()
        if item.reservation_id == reservation_id
    ]
    if len(matches) != 1:
        return "NOT_FOUND_OR_AMBIGUOUS"
    item = matches[0]
    if item.operation_binding != operation_id:
        return "BINDING_MISMATCH"
    if item.is_released:
        return "ALREADY_RELEASED"
    released = reservations.release(
        reservation_id, now=now, expected_binding=operation_id
    )
    return "RELEASED" if released else "NOT_RELEASED"
