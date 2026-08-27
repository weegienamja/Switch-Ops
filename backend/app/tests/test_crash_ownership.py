"""Can a new process prove what the dead one owned -- and refuse when it cannot?

The dangerous outcome here is not "failed to clean up". It is "deleted somebody
else's address because the record looked close enough". So the shape of this
file is deliberately lopsided: one test establishes that an exact, fully
evidenced row may be removed, and everything else establishes that a row missing
any single component of that proof may not be.

The central adversarial case is `test_a_row_recreated_by_another_actor_is_not_ours`:
SwitchOps creates an address, dies, the row disappears, and somebody else
creates the same address on the same adapter. Interface LUID, index, address and
prefix are then all identical, and only the OS-assigned creation timestamp can
tell the two rows apart.

Everything is synthetic: invented GUIDs, invented environment ids, RFC 5737
documentation addresses and RFC 2544 benchmarking addresses.
"""
from __future__ import annotations

import dataclasses
from datetime import datetime, timedelta, timezone

import pytest

from backend.recovery_lab.coexistence import NetworkSnapshot
from backend.recovery_lab.crash_reconcile import (
    Adjudication,
    Verdict,
    adjudicate,
    reconcile_after_crash,
)
from backend.recovery_lab.journal import (
    JournalRecordNotFound,
    JournalTransitionError,
    OwnedAddress,
    RecoveryJournal,
    JournalUnreadable,
    SCHEMA_VERSION,
    fingerprint_addresses,
    fingerprint_network_snapshot,
    fingerprint_row,
    now_iso,
)
from backend.recovery_lab.ownership_lock import (
    LockUnavailable,
    OperationLock,
    owner_process_is_gone,
)
from backend.recovery_lab.reservation import (
    LabReservationRegistry,
    ReservationStateError,
)
from backend.recovery_lab.windows_unicast import NO_ERROR, UnicastAddress

NOW = datetime(2026, 8, 27, 12, 0, tzinfo=timezone.utc)

IFINDEX = 58
LUID = 0x3A00000000000000
OTHER_LUID = 0x3B00000000000000
ALIAS = "synthetic-lab-adapter"
GUID = "11111111-2222-4333-8444-555555555555"
OTHER_GUID = "99999999-8888-4777-8666-555555555555"
ENVIRONMENT = "synthetic-environment-0001"
OTHER_ENVIRONMENT = "synthetic-environment-0002"
OPERATION = "recovery-op-synthetic00000001"
RESERVATION = "gate3-res-synthetic0001"

PRIMARY = "198.18.0.101"        # RFC 2544 benchmarking space, DHCP-served
OWNED = "192.0.2.250"           # RFC 5737 documentation space
PREFIX = 24

#: The moment Windows stamped on the row we created. Any other value is a
#: different object that happens to share an address.
CREATED_TS = 134322627952300878
OTHER_TS = 134322999999999999


def _row(address, *, origin=("MANUAL", "MANUAL"), dad="PREFERRED", prefix=PREFIX,
         index=IFINDEX, luid=LUID, ts=CREATED_TS, lifetime=0xFFFFFFFF):
    return UnicastAddress(
        address=address, prefix_length=prefix, interface_index=index,
        interface_luid=luid, prefix_origin=origin[0], suffix_origin=origin[1],
        dad_state=dad, valid_lifetime=lifetime, preferred_lifetime=lifetime,
        skip_as_source=False, creation_timestamp=ts,
    )


def _dhcp_primary(**kw):
    kw.setdefault("origin", ("DHCP", "DHCP"))
    kw.setdefault("lifetime", 3600)
    kw.setdefault("ts", 134322000000000000)
    return _row(PRIMARY, **kw)


def _snapshot(*, default_routes=((16, "198.18.0.1"),)) -> NetworkSnapshot:
    return NetworkSnapshot(
        interface_addresses=((PRIMARY, PREFIX),),
        interface_routes=("198.18.0.0/24",),
        default_routes=default_routes,
        dns_servers=("198.18.0.53",),
    )


def _record(**overrides) -> OwnedAddress:
    """A fully evidenced record: the state after a crash from window D onward."""
    owned_row = _row(OWNED)
    payload = dict(
        operation_id=OPERATION,
        plan_id="crash-run-0001",
        interface_alias=ALIAS,
        interface_index=IFINDEX,
        interface_luid=LUID,
        address=OWNED,
        prefix_length=PREFIX,
        created_at=now_iso(),
        state="ADDRESS_CREATED",
        previous_state_fingerprint=fingerprint_addresses([(PRIMARY, PREFIX)]),
        baseline_primary_address=PRIMARY,
        baseline_primary_prefix_length=PREFIX,
        previous_network_fingerprint=fingerprint_network_snapshot(_snapshot()),
        environment_id=ENVIRONMENT,
        interface_guid=GUID,
        reservation_id=RESERVATION,
        creation_timestamp=owned_row.creation_timestamp,
        post_apply_fingerprint=fingerprint_row(owned_row),
    )
    payload.update(overrides)
    return OwnedAddress(**payload)


def _intent_record(**overrides) -> OwnedAddress:
    """A clean write-before-create record accepted by the real journal API."""
    overrides = {
        "state": "INTENT_RECORDED",
        "creation_timestamp": 0,
        "post_apply_fingerprint": "",
        "closed_reason": "",
        **overrides,
    }
    return _record(**overrides)


def _persist_record(journal, record: OwnedAddress) -> None:
    """Build synthetic durable state through the production state machine."""
    journal.record_intent(
        dataclasses.replace(
            record,
            state="INTENT_RECORDED",
            creation_timestamp=0,
            post_apply_fingerprint="",
            closed_reason="",
        )
    )
    if record.has_post_apply_evidence:
        journal.record_created(
            record.operation_id,
            creation_timestamp=record.creation_timestamp,
            post_apply_fingerprint=record.post_apply_fingerprint,
        )
    elif record.state != "INTENT_RECORDED":
        journal.update_state(record.operation_id, "ADDRESS_CREATED")
    if record.state == "ADDRESS_VERIFIED":
        journal.update_state(record.operation_id, "ADDRESS_VERIFIED")
    elif record.state == "ROLLBACK_STARTED":
        journal.update_state(record.operation_id, "ROLLBACK_STARTED")
    elif record.state == "COMPLETED":
        journal.close(record.operation_id, record.closed_reason or "synthetic close")


def _adjudicate(record=None, *, rows=None, **overrides) -> Adjudication:
    record = record if record is not None else _record()
    kwargs = dict(
        rows=rows if rows is not None else [_dhcp_primary(), _row(OWNED)],
        all_records=[record],
        environment_authority_granted=True,
        environment_id=ENVIRONMENT,
        live_interface_guid=GUID,
        live_interface_index=IFINDEX,
        live_interface_luid=LUID,
        owner_process_gone=True,
    )
    kwargs.update(overrides)
    return adjudicate(record, **kwargs)


# --- the one permitted outcome ---------------------------------------------

def test_an_exactly_proven_row_may_be_removed():
    verdict = _adjudicate()
    assert verdict.verdict == "DELETE_AUTHORISED"
    assert verdict.may_delete is True
    assert verdict.refusals == []


def test_dad_settling_after_the_record_was_written_is_not_a_mismatch():
    # The crash may happen while the row is still tentative. A row that reached
    # preferred on its own afterwards is the same row.
    tentative = _row(OWNED, dad="TENTATIVE")
    record = _record(
        creation_timestamp=tentative.creation_timestamp,
        post_apply_fingerprint=fingerprint_row(tentative),
    )
    verdict = _adjudicate(record, rows=[_dhcp_primary(), _row(OWNED, dad="PREFERRED")])
    assert verdict.verdict == "DELETE_AUTHORISED"


# --- the central adversarial case ------------------------------------------

def test_a_row_recreated_by_another_actor_is_not_ours():
    """Same address, same adapter, same prefix -- different object.

    This is the case the whole post-apply fingerprint exists for. Everything an
    intent record could know is identical; only the timestamp the OS assigned
    the row differs.
    """
    impostor = _row(OWNED, ts=OTHER_TS)
    verdict = _adjudicate(rows=[_dhcp_primary(), impostor])
    assert verdict.verdict == "CONTRADICTED"
    assert "ROW_CREATION_TIMESTAMP_MISMATCH" in verdict.refusals
    assert verdict.may_delete is False


def test_intent_alone_cannot_authorise_deleting_a_matching_row():
    # Crash between create and the post-apply write. The row may well be ours,
    # and there is no way to prove it, so it is left alone.
    record = _record(
        state="INTENT_RECORDED", creation_timestamp=0, post_apply_fingerprint=""
    )
    verdict = _adjudicate(record)
    assert verdict.verdict == "OWNERSHIP_UNPROVABLE"
    assert "NO_POST_APPLY_EVIDENCE" in verdict.refusals


def test_a_zero_creation_timestamp_never_counts_as_evidence():
    record = _record(creation_timestamp=0)
    assert record.has_post_apply_evidence is False
    assert _adjudicate(record).verdict == "OWNERSHIP_UNPROVABLE"


# --- every component of the identity is load-bearing -----------------------

@pytest.mark.parametrize(
    "mutation,expected_refusal",
    [
        ({"address": "192.0.2.251"}, None),
        ({"prefix_length": 25}, None),
        ({"interface_index": 59}, None),
        ({"interface_luid": OTHER_LUID}, None),
        ({"creation_timestamp": OTHER_TS}, "ROW_CREATION_TIMESTAMP_MISMATCH"),
    ],
)
def test_changing_any_component_of_the_identity_removes_authority(
    mutation, expected_refusal
):
    """No single field is decorative: change one and deletion is unreachable."""
    record = _record(**mutation)
    if "creation_timestamp" not in mutation:
        # Keep the fingerprint self-consistent so the failure is the identity,
        # not an incidentally stale hash.
        row = _row(
            record.address,
            prefix=record.prefix_length,
            index=record.interface_index,
            luid=record.interface_luid,
        )
        record = dataclasses.replace(
            record,
            creation_timestamp=row.creation_timestamp,
            post_apply_fingerprint=fingerprint_row(row),
        )
    verdict = _adjudicate(record)
    assert verdict.may_delete is False, mutation
    if expected_refusal:
        assert expected_refusal in verdict.refusals


def test_a_stale_fingerprint_over_the_right_timestamp_still_refuses():
    record = _record(post_apply_fingerprint="0" * 32)
    verdict = _adjudicate(record)
    assert verdict.verdict == "CONTRADICTED"
    assert "ROW_FINGERPRINT_MISMATCH" in verdict.refusals


@pytest.mark.parametrize("origin", [("DHCP", "DHCP"), ("WELL_KNOWN", "RANDOM")])
def test_a_row_the_harness_would_never_create_is_refused(origin):
    # The harness only ever makes MANUAL/MANUAL rows. Anything else contradicts
    # ownership regardless of what the record says.
    contradicting = _row(OWNED, origin=origin)
    record = _record(post_apply_fingerprint=fingerprint_row(contradicting))
    verdict = _adjudicate(record, rows=[_dhcp_primary(), contradicting])
    assert verdict.verdict == "CONTRADICTED"
    assert "ROW_ORIGIN_CONTRADICTS_OWNERSHIP" in verdict.refusals


# --- absence ----------------------------------------------------------------

def test_an_absent_row_produces_no_delete():
    verdict = _adjudicate(rows=[_dhcp_primary()])
    assert verdict.verdict == "ALREADY_ABSENT"
    assert verdict.may_delete is False


def test_the_same_address_on_another_interface_is_left_alone():
    """Absence must never turn into a search for something similar."""
    verdict = _adjudicate(
        rows=[_dhcp_primary(), _row(OWNED, index=99, luid=OTHER_LUID)]
    )
    assert verdict.verdict == "ALREADY_ABSENT"
    assert "ADDRESS_PRESENT_ON_ANOTHER_INTERFACE" in verdict.refusals
    assert verdict.may_delete is False


def test_absence_is_reported_without_claiming_to_know_why():
    joined = " ".join(_adjudicate(rows=[_dhcp_primary()]).evidence)
    assert "absent" in joined
    # No inference about reboots, NIC resets or anything else unmeasured.
    for unmeasured in ("reboot", "NIC reset", "reclaimed"):
        assert unmeasured not in joined


# --- environment authority --------------------------------------------------

def test_an_unproven_environment_refuses_everything():
    verdict = _adjudicate(environment_authority_granted=False)
    assert verdict.verdict == "ENVIRONMENT_NOT_AUTHORISED"
    assert "ENVIRONMENT_AUTHORITY_REFUSED" in verdict.refusals


def test_a_record_from_another_environment_is_refused():
    verdict = _adjudicate(environment_id=OTHER_ENVIRONMENT)
    assert verdict.verdict == "ENVIRONMENT_NOT_AUTHORISED"
    assert "ENVIRONMENT_MISMATCH" in verdict.refusals


def test_a_record_that_names_no_environment_cannot_be_re_proven():
    verdict = _adjudicate(_record(environment_id=""))
    assert verdict.verdict == "OWNERSHIP_UNPROVABLE"
    assert "ENVIRONMENT_RECORD_ABSENT" in verdict.refusals


def test_a_different_adapter_guid_contradicts_the_record():
    verdict = _adjudicate(live_interface_guid=OTHER_GUID)
    assert verdict.verdict == "CONTRADICTED"
    assert "INTERFACE_GUID_MISMATCH" in verdict.refusals


def test_an_unresolvable_adapter_identity_refuses():
    verdict = _adjudicate(live_interface_guid=None)
    assert verdict.verdict == "ENVIRONMENT_NOT_AUTHORISED"
    assert "INTERFACE_GUID_UNRESOLVED" in verdict.refusals


def test_a_record_with_no_guid_refuses():
    verdict = _adjudicate(_record(interface_guid=""))
    assert verdict.verdict == "ENVIRONMENT_NOT_AUTHORISED"
    assert "INTERFACE_GUID_UNRESOLVED" in verdict.refusals


def test_guid_comparison_ignores_braces_and_case():
    # VirtualBox and Windows spell the same GUID differently. That is
    # formatting, not a different adapter.
    verdict = _adjudicate(live_interface_guid="{" + GUID.upper() + "}")
    assert verdict.verdict == "DELETE_AUTHORISED"


def test_an_alias_rename_alone_does_not_break_ownership():
    # The alias is a label. The GUID, LUID, index, address, prefix and creation
    # timestamp all still match, so the row is still ours.
    verdict = _adjudicate(_record(interface_alias="Ethernet 47"))
    assert verdict.verdict == "DELETE_AUTHORISED"


@pytest.mark.parametrize(
    "overrides,refusal",
    [
        ({"live_interface_index": None}, "LIVE_INTERFACE_IDENTITY_UNRESOLVED"),
        ({"live_interface_luid": None}, "LIVE_INTERFACE_IDENTITY_UNRESOLVED"),
        ({"live_interface_index": IFINDEX + 1}, "LIVE_INTERFACE_INDEX_MISMATCH"),
        ({"live_interface_luid": OTHER_LUID}, "LIVE_INTERFACE_LUID_MISMATCH"),
    ],
)
def test_live_interface_identity_must_still_match(overrides, refusal):
    verdict = _adjudicate(**overrides)
    assert verdict.may_delete is False
    assert refusal in verdict.refusals


def test_same_address_and_interface_with_the_wrong_prefix_is_a_contradiction():
    verdict = _adjudicate(
        rows=[_dhcp_primary(), _row(OWNED, prefix=PREFIX + 1)]
    )
    assert verdict.verdict == "CONTRADICTED"
    assert verdict.refusals == ["ROW_PREFIX_MISMATCH"]


@pytest.mark.parametrize(
    "missing",
    [
        "previous_state_fingerprint",
        "baseline_primary_address",
        "previous_network_fingerprint",
    ],
)
def test_missing_restoration_evidence_removes_delete_authority(missing):
    record = dataclasses.replace(_record(), **{missing: ""})
    verdict = _adjudicate(record)
    assert verdict.verdict == "OWNERSHIP_UNPROVABLE"
    assert "BASELINE_EVIDENCE_ABSENT" in verdict.refusals


# --- liveness and ambiguity -------------------------------------------------

def test_a_live_owner_is_never_reconciled_out_from_under():
    verdict = _adjudicate(owner_process_gone=False)
    assert verdict.verdict == "OWNER_PROCESS_ALIVE"
    assert "OWNER_PROCESS_STILL_RUNNING" in verdict.refusals


def test_two_records_claiming_the_same_row_refuse_rather_than_choose():
    first = _record()
    second = _record(operation_id="recovery-op-synthetic00000002")
    verdict = adjudicate(
        first,
        rows=[_dhcp_primary(), _row(OWNED)],
        all_records=[first, second],
        environment_authority_granted=True,
        environment_id=ENVIRONMENT,
        live_interface_guid=GUID,
        live_interface_index=IFINDEX,
        live_interface_luid=LUID,
        owner_process_gone=True,
    )
    assert verdict.verdict == "AMBIGUOUS_CLAIM"
    assert "DUPLICATE_CLAIMANT" in verdict.refusals


def test_a_closed_duplicate_does_not_create_ambiguity():
    first = _record()
    closed = _record(operation_id="recovery-op-synthetic00000002", state="COMPLETED")
    verdict = adjudicate(
        first,
        rows=[_dhcp_primary(), _row(OWNED)],
        all_records=[first, closed],
        environment_authority_granted=True,
        environment_id=ENVIRONMENT,
        live_interface_guid=GUID,
        live_interface_index=IFINDEX,
        live_interface_luid=LUID,
        owner_process_gone=True,
    )
    assert verdict.verdict == "DELETE_AUTHORISED"


def test_an_already_closed_record_claims_nothing():
    verdict = _adjudicate(_record(state="COMPLETED"))
    assert verdict.verdict == "ALREADY_CLOSED"
    assert verdict.may_delete is False


def test_two_identical_live_rows_refuse_rather_than_pick_one():
    verdict = _adjudicate(rows=[_dhcp_primary(), _row(OWNED), _row(OWNED)])
    assert verdict.verdict == "AMBIGUOUS_CLAIM"
    assert "MULTIPLE_MATCHING_ROWS" in verdict.refusals


# --- the invariant, stated directly ----------------------------------------

def test_delete_authority_requires_every_predicate_at_once():
    """Turn off any one precondition and the verdict stops being a delete."""
    disablers = [
        {"environment_authority_granted": False},
        {"environment_id": OTHER_ENVIRONMENT},
        {"live_interface_guid": OTHER_GUID},
        {"owner_process_gone": False},
        {"rows": [_dhcp_primary()]},
        {"rows": [_dhcp_primary(), _row(OWNED, ts=OTHER_TS)]},
        {"rows": [_dhcp_primary(), _row(OWNED, origin=("DHCP", "DHCP"))]},
    ]
    assert _adjudicate().may_delete is True
    for disabler in disablers:
        assert _adjudicate(**disabler).may_delete is False, disabler

    record_disablers = [
        {"state": "COMPLETED"},
        {"environment_id": ""},
        {"interface_guid": ""},
        {"creation_timestamp": 0},
        {"post_apply_fingerprint": ""},
        {"post_apply_fingerprint": "0" * 32},
        {"address": "192.0.2.251"},
        {"prefix_length": 25},
        {"interface_index": 59},
        {"interface_luid": OTHER_LUID},
    ]
    for disabler in record_disablers:
        assert _adjudicate(_record(**disabler)).may_delete is False, disabler


def test_no_verdict_other_than_delete_authorised_permits_a_mutation():
    import typing

    for verdict in typing.get_args(Verdict):
        assert Adjudication(verdict=verdict).may_delete is (
            verdict == "DELETE_AUTHORISED"
        )


# --- journal ordering and durability ---------------------------------------

def test_intent_is_durable_before_the_address_exists(tmp_path):
    """The record must be readable by another process before the create call."""
    journal = RecoveryJournal(tmp_path / "journal.json")
    journal.record_intent(_intent_record())
    # A completely separate handle, as a new process would have.
    reread = RecoveryJournal(tmp_path / "journal.json").outstanding()
    assert [item.operation_id for item in reread] == [OPERATION]
    assert reread[0].state == "INTENT_RECORDED"
    assert reread[0].has_post_apply_evidence is False


def test_post_apply_evidence_becomes_durable_in_one_write(tmp_path):
    journal = RecoveryJournal(tmp_path / "journal.json")
    journal.record_intent(_intent_record())
    row = _row(OWNED)
    journal.record_created(
        OPERATION,
        creation_timestamp=row.creation_timestamp,
        post_apply_fingerprint=fingerprint_row(row),
    )
    reread = RecoveryJournal(tmp_path / "journal.json").outstanding()[0]
    assert reread.state == "ADDRESS_CREATED"
    assert reread.has_post_apply_evidence is True
    assert reread.creation_timestamp == row.creation_timestamp


def test_placeholder_post_apply_evidence_is_refused(tmp_path):
    journal = RecoveryJournal(tmp_path / "journal.json")
    journal.record_intent(_intent_record())
    with pytest.raises(ValueError):
        journal.record_created(
            OPERATION, creation_timestamp=0, post_apply_fingerprint="x" * 32
        )
    with pytest.raises(ValueError):
        journal.record_created(
            OPERATION, creation_timestamp=CREATED_TS, post_apply_fingerprint=""
        )


def test_a_corrupt_journal_is_never_read_as_nothing_owned(tmp_path):
    path = tmp_path / "journal.json"
    path.write_text("{not json", encoding="utf-8")
    with pytest.raises(JournalUnreadable):
        RecoveryJournal(path).outstanding()


def test_a_truncated_journal_is_refused(tmp_path):
    path = tmp_path / "journal.json"
    path.write_text('[{"operation_id": "recovery-op-x", "schema', encoding="utf-8")
    with pytest.raises(JournalUnreadable):
        RecoveryJournal(path).outstanding()


def test_a_journal_of_the_wrong_shape_is_refused(tmp_path):
    path = tmp_path / "journal.json"
    path.write_text('{"operation_id": "recovery-op-x"}', encoding="utf-8")
    with pytest.raises(JournalUnreadable):
        RecoveryJournal(path).outstanding()


@pytest.mark.parametrize(
    "mutation",
    [
        lambda record: record.update({"unexpected_authority": True}),
        lambda record: record.update({"state": "SOMEBODY_ELSES_STATE"}),
        lambda record: record.update({"interface_index": "58"}),
        lambda record: record.update({"notes": ["valid", 7]}),
        lambda record: record.update({"post_apply_fingerprint": "not-a-hash"}),
    ],
)
def test_malformed_known_schema_records_fail_closed(tmp_path, mutation):
    import json

    record = dataclasses.asdict(_record())
    mutation(record)
    path = tmp_path / "journal.json"
    path.write_text(json.dumps([record]), encoding="utf-8")
    with pytest.raises(JournalUnreadable):
        RecoveryJournal(path).outstanding()


def test_intent_state_cannot_smuggle_in_post_apply_evidence(tmp_path):
    import json

    record = dataclasses.asdict(_record(state="INTENT_RECORDED"))
    path = tmp_path / "journal.json"
    path.write_text(json.dumps([record]), encoding="utf-8")
    with pytest.raises(JournalUnreadable):
        RecoveryJournal(path).outstanding()


@pytest.mark.parametrize("version", [None, 1, 99, "2"])
def test_an_unknown_schema_version_is_refused_not_guessed_at(tmp_path, version):
    import json

    path = tmp_path / "journal.json"
    record = dataclasses.asdict(_record())
    if version is None:
        record.pop("schema_version")
    else:
        record["schema_version"] = version
    path.write_text(json.dumps([record]), encoding="utf-8")
    with pytest.raises(JournalUnreadable):
        RecoveryJournal(path).outstanding()


def test_the_current_schema_version_reads_back(tmp_path):
    journal = RecoveryJournal(tmp_path / "journal.json")
    journal.record_intent(_intent_record())
    assert RecoveryJournal(tmp_path / "journal.json").outstanding()[0].schema_version == (
        SCHEMA_VERSION
    )


def test_an_absent_journal_is_empty_rather_than_an_error(tmp_path):
    # Absent is different from corrupt: nothing was ever claimed.
    assert RecoveryJournal(tmp_path / "nope.json").outstanding() == []


def test_closing_keeps_the_record_and_stops_the_claim(tmp_path):
    journal = RecoveryJournal(tmp_path / "journal.json")
    _persist_record(journal, _record())
    journal.close(OPERATION, "reconciled after crash")
    assert journal.outstanding() == []
    kept = journal.all()
    assert len(kept) == 1
    assert kept[0].state == "COMPLETED"
    assert kept[0].closed_reason == "reconciled after crash"


def test_an_operation_id_can_never_be_reused(tmp_path):
    """Replaying an id would let a new run inherit an old record's evidence."""
    journal = RecoveryJournal(tmp_path / "journal.json")
    journal.record_intent(_intent_record())
    with pytest.raises(JournalUnreadable):
        journal.record_intent(_intent_record(address="192.0.2.251"))


def test_lifecycle_writes_cannot_manufacture_a_missing_operation(tmp_path):
    journal = RecoveryJournal(tmp_path / "journal.json")
    with pytest.raises(JournalRecordNotFound):
        journal.record_created(
            OPERATION,
            creation_timestamp=CREATED_TS,
            post_apply_fingerprint=fingerprint_row(_row(OWNED)),
        )
    with pytest.raises(JournalRecordNotFound):
        journal.update_state(OPERATION, "ROLLBACK_STARTED")
    with pytest.raises(JournalRecordNotFound):
        journal.close(OPERATION, "not actually present")
    with pytest.raises(JournalRecordNotFound):
        journal.clear(OPERATION)


def test_record_intent_rejects_prepopulated_creation_evidence(tmp_path):
    journal = RecoveryJournal(tmp_path / "journal.json")
    with pytest.raises(JournalTransitionError):
        journal.record_intent(_record())


def test_an_active_ownership_record_cannot_be_cleared(tmp_path):
    journal = RecoveryJournal(tmp_path / "journal.json")
    journal.record_intent(_intent_record())
    with pytest.raises(JournalTransitionError):
        journal.clear(OPERATION)
    assert journal.outstanding()[0].operation_id == OPERATION


def test_a_closed_operation_id_still_cannot_be_reused(tmp_path):
    journal = RecoveryJournal(tmp_path / "journal.json")
    journal.record_intent(_intent_record())
    journal.close(OPERATION, "done")
    with pytest.raises(JournalUnreadable):
        journal.record_intent(_intent_record())


def test_a_partial_write_never_replaces_a_good_journal(tmp_path):
    """A crash during persistence leaves the previous content intact."""
    path = tmp_path / "journal.json"
    journal = RecoveryJournal(path)
    journal.record_intent(_intent_record())
    good = path.read_text(encoding="utf-8")

    original = journal._write

    def explode(records):
        raise KeyboardInterrupt("process died mid-write")

    journal._write = explode
    with pytest.raises(KeyboardInterrupt):
        journal.close(OPERATION, "interrupted")
    journal._write = original

    assert path.read_text(encoding="utf-8") == good
    assert RecoveryJournal(path).outstanding()[0].operation_id == OPERATION


def test_temp_files_are_unique_per_writer(tmp_path):
    # A shared temp name lets two writers corrupt each other and then publish
    # the result atomically, which is worse than not being atomic at all.
    path = tmp_path / "journal.json"
    journal = RecoveryJournal(path)
    journal.record_intent(_intent_record())
    leftovers = list(tmp_path.glob("*.tmp"))
    assert leftovers == []


# --- concurrency ------------------------------------------------------------

def test_concurrent_writers_do_not_lose_each_others_records(tmp_path):
    """The defect this locking exists for: a lost intent record.

    A lost record means an address exists that nothing on disk claims -- which
    is precisely the orphan the journal is supposed to make impossible.
    """
    path = tmp_path / "journal.json"
    first = RecoveryJournal(path)
    second = RecoveryJournal(path)
    first.record_intent(
        _intent_record(operation_id="recovery-op-a", address="192.0.2.10")
    )
    second.record_intent(
        _intent_record(operation_id="recovery-op-b", address="192.0.2.11")
    )
    ids = {item.operation_id for item in RecoveryJournal(path).outstanding()}
    assert ids == {"recovery-op-a", "recovery-op-b"}


def test_two_process_journal_writers_do_not_lose_an_intent(tmp_path):
    import pathlib
    import subprocess
    import sys

    path = tmp_path / "journal.json"
    start = tmp_path / "start"
    repo = str(pathlib.Path(__file__).resolve().parents[3])
    child = r'''
import pathlib, sys, time
sys.path.insert(0, sys.argv[1])
from backend.recovery_lab.journal import OwnedAddress, RecoveryJournal
start = pathlib.Path(sys.argv[3])
deadline = time.monotonic() + 10
while not start.exists() and time.monotonic() < deadline:
    time.sleep(0.01)
RecoveryJournal(pathlib.Path(sys.argv[2])).record_intent(OwnedAddress(
    operation_id=sys.argv[4], plan_id="synthetic-plan",
    interface_alias="synthetic-adapter", interface_index=58,
    interface_luid=0x3A00000000000000, address=sys.argv[5], prefix_length=24,
    created_at="2026-08-27T12:00:00Z", state="INTENT_RECORDED"))
'''
    processes = [
        subprocess.Popen(
            [
                sys.executable,
                "-c",
                child,
                repo,
                str(path),
                str(start),
                operation,
                address,
            ],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )
        for operation, address in (
            ("recovery-op-process-a", "192.0.2.10"),
            ("recovery-op-process-b", "192.0.2.11"),
        )
    ]
    start.write_text("go", encoding="utf-8")
    for process in processes:
        _stdout, stderr = process.communicate(timeout=15)
        assert process.returncode == 0, stderr
    ids = {item.operation_id for item in RecoveryJournal(path).outstanding()}
    assert ids == {"recovery-op-process-a", "recovery-op-process-b"}


def test_a_held_operation_lock_blocks_a_second_claimant(tmp_path):
    lock = OperationLock(tmp_path / "locks", OPERATION)
    lock.acquire()
    try:
        assert owner_process_is_gone(tmp_path / "locks", OPERATION) is False
        rival = OperationLock(tmp_path / "locks", OPERATION)
        with pytest.raises(LockUnavailable):
            rival.acquire()
    finally:
        lock.release()
    assert owner_process_is_gone(tmp_path / "locks", OPERATION) is True


def test_an_operation_lock_is_released_when_its_process_dies(tmp_path):
    """The liveness primitive, measured against a real hard exit.

    This is how a reconciler tells "the owner crashed" from "the owner is still
    working": the kernel drops the lock when the process ends, so nothing has to
    guess whether a lock file is stale.
    """
    import subprocess
    import sys

    directory = tmp_path / "locks"
    child = (
        "import sys;"
        "sys.path.insert(0, sys.argv[1]);"
        "from backend.recovery_lab.ownership_lock import OperationLock;"
        "import os;"
        "lock = OperationLock(sys.argv[2], sys.argv[3]);"
        "lock.acquire();"
        "print('held', flush=True);"
        "os._exit(89)"
    )
    import pathlib

    repo = str(pathlib.Path(__file__).resolve().parents[3])
    completed = subprocess.run(
        [sys.executable, "-c", child, repo, str(directory), OPERATION],
        capture_output=True, text=True,
    )
    assert completed.returncode == 89, completed.stderr
    assert "held" in completed.stdout
    # No cleanup ran in the child; the kernel released the lock anyway.
    assert owner_process_is_gone(directory, OPERATION) is True


def test_a_missing_lock_file_is_not_evidence_of_a_live_process(tmp_path):
    assert owner_process_is_gone(tmp_path / "locks", "recovery-op-never-existed") is True


def test_operation_ids_are_data_not_paths(tmp_path):
    directory = tmp_path / "locks"
    lock = OperationLock(directory, "../../outside-the-lock-directory")
    lock.acquire()
    try:
        assert lock.path.parent == directory
        assert ".." not in lock.path.name
    finally:
        lock.release()


def test_crash_status_does_not_create_missing_state_paths(tmp_path, capsys):
    from types import SimpleNamespace

    from backend.recovery_lab.__main__ import command_crash_status

    state = tmp_path / "never-created" / "state"
    args = SimpleNamespace(
        journal=str(state / "journal.json"),
        reservations=str(state / "reservations.json"),
    )
    assert command_crash_status(args) == 0
    assert "outstanding: 0" in capsys.readouterr().out
    assert state.exists() is False


# --- end-to-end reconciliation ---------------------------------------------

class World:
    """A fake adapter whose table responds to deletion."""

    def __init__(self, rows=None, delete_code=NO_ERROR, delete_removes=True):
        self.rows = list(rows if rows is not None else [_dhcp_primary(), _row(OWNED)])
        self.delete_code = delete_code
        self.delete_removes = delete_removes
        self.deletes: list[dict] = []

    def read_table(self):
        return list(self.rows)

    def read_snapshot(self):
        return _snapshot()

    def delete(self, *, address, prefix_length, interface_index, interface_luid):
        self.deletes.append({"address": address, "prefix_length": prefix_length,
                             "interface_index": interface_index,
                             "interface_luid": interface_luid})
        if self.delete_code != NO_ERROR:
            return self.delete_code
        if self.delete_removes:
            self.rows = [
                row for row in self.rows
                if not (row.address == address and row.interface_luid == interface_luid)
            ]
        return NO_ERROR


def _reservations(tmp_path, *, bind_to=OPERATION, release=False):
    registry = LabReservationRegistry(tmp_path / "reservations.json")
    _outcome, reservation, _evidence = registry.issue(
        address=OWNED, target_prefix="192.0.2.0/24", environment_id=ENVIRONMENT,
        attested_by="synthetic recovery lab harness",
        evidence_reference="crash-isolated-experiment", now=NOW,
    )
    if bind_to:
        registry.bind(reservation.reservation_id, bind_to)
    if release:
        registry.release(reservation.reservation_id, now=NOW)
    return registry, reservation


def _reconcile(tmp_path, world, record=None, *, registry=None, **overrides):
    journal = RecoveryJournal(tmp_path / "journal.json")
    record = record if record is not None else _record()
    _persist_record(journal, record)
    if registry is None:
        registry, _ = _reservations(tmp_path)
    kwargs = dict(
        journal=journal,
        reservations=registry,
        read_table=world.read_table,
        read_snapshot=world.read_snapshot,
        delete=world.delete,
        environment_authority_granted=True,
        environment_id=ENVIRONMENT,
        live_interface_guid=GUID,
        live_interface_index=IFINDEX,
        live_interface_luid=LUID,
        now=NOW,
    )
    kwargs.update(overrides)
    return reconcile_after_crash(**kwargs), journal, registry


def test_a_new_process_removes_exactly_the_owned_row(tmp_path):
    registry, reservation = _reservations(tmp_path)
    record = _record(reservation_id=reservation.reservation_id)
    world = World()
    result, journal, registry = _reconcile(
        tmp_path, world, record, registry=registry
    )
    assert result.outcome == "RECONCILED"
    assert result.deletes_attempted == 1
    assert [entry["address"] for entry in world.deletes] == [OWNED]
    assert result.outstanding_after == 0
    # The DHCP primary was never a candidate for deletion.
    assert [row.address for row in world.rows] == [PRIMARY]


def test_reconciliation_closes_the_journal_only_after_proving_absence(tmp_path):
    world = World(delete_removes=False)  # delete reports success but changes nothing
    result, journal, _ = _reconcile(tmp_path, world)
    assert result.deletes_attempted == 1
    assert result.outcome == "BLOCKED"
    # The claim survives, because the address does.
    assert len(journal.outstanding()) == 1


def test_a_failed_delete_leaves_the_claim_open(tmp_path):
    world = World(delete_code=5)  # ERROR_ACCESS_DENIED
    result, journal, _ = _reconcile(tmp_path, world)
    assert result.outcome == "BLOCKED"
    assert len(journal.outstanding()) == 1


def test_table_read_failure_is_blocked_with_zero_deletes(tmp_path):
    world = World()

    def unreadable_table():
        raise OSError("synthetic table read failure")

    world.read_table = unreadable_table
    result, journal, _ = _reconcile(tmp_path, world)
    assert result.outcome == "BLOCKED"
    assert result.deletes_attempted == 0
    assert world.deletes == []
    assert "OBSERVATION_FAILED" in result.records[0].refusals
    assert len(journal.outstanding()) == 1


def test_delete_exception_leaves_the_claim_open(tmp_path):
    world = World()

    def exploding_delete(**kwargs):
        raise OSError("synthetic delete failure")

    world.delete = exploding_delete
    result, journal, _ = _reconcile(tmp_path, world)
    assert result.outcome == "BLOCKED"
    assert result.deletes_attempted == 1
    assert "OBSERVATION_FAILED" in result.records[0].refusals
    assert len(journal.outstanding()) == 1


def test_an_already_absent_row_closes_without_any_delete(tmp_path):
    world = World(rows=[_dhcp_primary()])
    result, journal, _ = _reconcile(tmp_path, world)
    assert result.outcome == "ALREADY_ABSENT"
    assert result.deletes_attempted == 0
    assert world.deletes == []
    assert journal.outstanding() == []


def test_crash_before_create_closes_intent_only_absence_with_zero_deletes(tmp_path):
    world = World(rows=[_dhcp_primary()])
    result, journal, _ = _reconcile(tmp_path, world, _intent_record())
    assert result.outcome == "ALREADY_ABSENT"
    assert result.deletes_attempted == 0
    assert world.deletes == []
    assert journal.outstanding() == []


@pytest.mark.parametrize(
    "overrides",
    [
        {"environment_authority_granted": False},
        {"environment_id": OTHER_ENVIRONMENT},
        {"live_interface_guid": OTHER_GUID},
    ],
)
def test_every_refusal_path_issues_zero_deletes(tmp_path, overrides):
    world = World()
    result, journal, _ = _reconcile(tmp_path, world, **overrides)
    assert result.deletes_attempted == 0
    assert world.deletes == []
    assert len(journal.outstanding()) == 1


def test_an_impostor_row_survives_reconciliation_untouched(tmp_path):
    world = World(rows=[_dhcp_primary(), _row(OWNED, ts=OTHER_TS)])
    result, journal, _ = _reconcile(tmp_path, world)
    assert result.outcome == "BLOCKED"
    assert world.deletes == []
    assert len(world.rows) == 2
    assert len(journal.outstanding()) == 1


def test_a_similar_row_elsewhere_is_never_swept_up(tmp_path):
    elsewhere = _row(OWNED, index=99, luid=OTHER_LUID)
    world = World(rows=[_dhcp_primary(), elsewhere])
    result, journal, _ = _reconcile(tmp_path, world)
    assert result.deletes_attempted == 0
    assert elsewhere in world.rows


def test_nothing_outstanding_is_a_clean_no_op(tmp_path):
    journal = RecoveryJournal(tmp_path / "journal.json")
    registry, _ = _reservations(tmp_path)
    world = World()
    result = reconcile_after_crash(
        journal=journal, reservations=registry, read_table=world.read_table,
        read_snapshot=world.read_snapshot, delete=world.delete,
        environment_authority_granted=True,
        environment_id=ENVIRONMENT, live_interface_guid=GUID,
        live_interface_index=IFINDEX, live_interface_luid=LUID, now=NOW,
    )
    assert result.outcome == "NOTHING_OUTSTANDING"
    assert result.deletes_attempted == 0


@pytest.mark.parametrize("payload", ["{not-json", '[{"schema_version": 99}]'])
def test_unreadable_or_unknown_journal_reaches_no_delete(tmp_path, payload):
    path = tmp_path / "journal.json"
    path.write_text(payload, encoding="utf-8")
    journal = RecoveryJournal(path)
    registry, _ = _reservations(tmp_path)
    world = World()
    with pytest.raises(JournalUnreadable):
        reconcile_after_crash(
            journal=journal,
            reservations=registry,
            read_table=world.read_table,
            read_snapshot=world.read_snapshot,
            delete=world.delete,
            environment_authority_granted=True,
            environment_id=ENVIRONMENT,
            live_interface_guid=GUID,
            live_interface_index=IFINDEX,
            live_interface_luid=LUID,
            now=NOW,
        )
    assert world.deletes == []


def test_a_live_owner_blocks_reconciliation_end_to_end(tmp_path):
    journal = RecoveryJournal(tmp_path / "journal.json")
    record = _record()
    _persist_record(journal, record)
    lock = journal.operation_lock(OPERATION)
    lock.acquire()
    try:
        registry, _ = _reservations(tmp_path)
        world = World()
        result = reconcile_after_crash(
            journal=journal, reservations=registry, read_table=world.read_table,
            read_snapshot=world.read_snapshot, delete=world.delete,
            environment_authority_granted=True,
            environment_id=ENVIRONMENT, live_interface_guid=GUID,
            live_interface_index=IFINDEX, live_interface_luid=LUID, now=NOW,
        )
        assert result.outcome == "BLOCKED"
        assert result.deletes_attempted == 0
        assert result.records[0].verdict == "OWNER_PROCESS_ALIVE"
    finally:
        lock.release()


def test_the_dhcp_primary_disappearing_blocks_closure(tmp_path):
    # Our row goes, but so has the lease. Something bigger happened; say so
    # rather than closing the book on it.
    world = World(rows=[_row(OWNED)])
    result, journal, _ = _reconcile(tmp_path, world)
    assert result.outcome == "BLOCKED"
    assert len(journal.outstanding()) == 1


class ScriptedWorld(World):
    """Returns a different atomic table observation on each read."""

    def __init__(self, tables, *, snapshot=None):
        super().__init__(rows=[], delete_removes=False)
        self.tables = [list(table) for table in tables]
        self.reads = 0
        self.snapshot = snapshot or _snapshot()

    def read_table(self):
        index = min(self.reads, len(self.tables) - 1)
        self.reads += 1
        return list(self.tables[index])

    def read_snapshot(self):
        return self.snapshot


def test_identity_changed_during_adjudication_means_zero_deletes(tmp_path):
    world = ScriptedWorld(
        [
            [_dhcp_primary(), _row(OWNED)],
            [_dhcp_primary(), _row(OWNED, ts=OTHER_TS)],
        ]
    )
    result, journal, _ = _reconcile(tmp_path, world)
    assert result.outcome == "BLOCKED"
    assert result.deletes_attempted == 0
    assert world.deletes == []
    assert "ROW_CREATION_TIMESTAMP_MISMATCH" in result.records[0].refusals
    assert len(journal.outstanding()) == 1


def test_an_absent_row_that_reappears_is_not_closed_or_deleted(tmp_path):
    world = ScriptedWorld(
        [
            [_dhcp_primary()],
            [_dhcp_primary(), _row(OWNED)],
        ]
    )
    result, journal, _ = _reconcile(tmp_path, world)
    assert result.outcome == "BLOCKED"
    assert result.deletes_attempted == 0
    assert world.deletes == []
    assert len(journal.outstanding()) == 1


def test_post_delete_recreation_is_a_contradiction_not_success(tmp_path):
    world = ScriptedWorld(
        [
            [_dhcp_primary(), _row(OWNED)],
            [_dhcp_primary(), _row(OWNED)],
            [_dhcp_primary(), _row(OWNED, ts=OTHER_TS)],
        ]
    )
    result, journal, _ = _reconcile(tmp_path, world)
    assert result.outcome == "BLOCKED"
    assert result.deletes_attempted == 1
    assert result.records[0].closed is False
    assert result.records[0].deleted is False
    assert len(journal.outstanding()) == 1


def test_primary_must_remain_the_exact_finite_dhcp_lease(tmp_path):
    non_lease_primary = _dhcp_primary(lifetime=0xFFFFFFFF)
    world = World(rows=[non_lease_primary, _row(OWNED)])
    result, journal, registry = _reconcile(tmp_path, world)
    assert result.outcome == "BLOCKED"
    assert result.deletes_attempted == 1
    assert result.records[0].closed is False
    assert len(journal.outstanding()) == 1
    assert registry.all()[0].is_released is False


def test_network_baseline_mismatch_keeps_bookkeeping_open(tmp_path):
    world = World()
    world.read_snapshot = lambda: _snapshot(default_routes=((17, "198.18.0.1"),))
    result, journal, registry = _reconcile(tmp_path, world)
    assert result.outcome == "BLOCKED"
    assert result.deletes_attempted == 1
    assert result.records[0].closed is False
    assert len(journal.outstanding()) == 1
    assert registry.all()[0].is_released is False


def test_two_process_reconcilers_cannot_both_acquire_delete_authority(tmp_path):
    import pathlib
    import subprocess
    import sys
    import time

    journal = RecoveryJournal(tmp_path / "journal.json")
    _persist_record(journal, _record())
    registry, _reservation = _reservations(tmp_path)
    ready = tmp_path / "child-ready"
    proceed = tmp_path / "child-proceed"
    deleted = tmp_path / "child-delete"
    repo = str(pathlib.Path(__file__).resolve().parents[3])
    child = r'''
import pathlib, sys, time
from datetime import datetime, timezone
sys.path.insert(0, sys.argv[1])
from backend.recovery_lab.coexistence import NetworkSnapshot
from backend.recovery_lab.crash_reconcile import reconcile_after_crash
from backend.recovery_lab.journal import RecoveryJournal
from backend.recovery_lab.reservation import LabReservationRegistry
from backend.recovery_lab.windows_unicast import NO_ERROR, UnicastAddress
journal_path, reservation_path, ready_path, proceed_path, deleted_path = sys.argv[2:]
ready = pathlib.Path(ready_path); proceed = pathlib.Path(proceed_path)
deleted = pathlib.Path(deleted_path)
rows = [
    UnicastAddress(address="198.18.0.101", prefix_length=24, interface_index=58,
        interface_luid=0x3A00000000000000, prefix_origin="DHCP",
        suffix_origin="DHCP", dad_state="PREFERRED", valid_lifetime=3600,
        preferred_lifetime=3600, skip_as_source=False,
        creation_timestamp=134322000000000000),
    UnicastAddress(address="192.0.2.250", prefix_length=24, interface_index=58,
        interface_luid=0x3A00000000000000, prefix_origin="MANUAL",
        suffix_origin="MANUAL", dad_state="PREFERRED", valid_lifetime=0xFFFFFFFF,
        preferred_lifetime=0xFFFFFFFF, skip_as_source=False,
        creation_timestamp=134322627952300878),
]
first = True
def read_table():
    global first
    if first:
        first = False
        ready.write_text("held", encoding="utf-8")
        deadline = time.monotonic() + 10
        while not proceed.exists() and time.monotonic() < deadline:
            time.sleep(0.02)
    return list(rows)
def read_snapshot():
    return NetworkSnapshot(interface_addresses=(("198.18.0.101", 24),),
        interface_routes=("198.18.0.0/24",),
        default_routes=((16, "198.18.0.1"),), dns_servers=("198.18.0.53",))
def delete(**kwargs):
    deleted.write_text("one", encoding="utf-8")
    rows[:] = [row for row in rows if not (
        row.address == kwargs["address"] and
        row.interface_luid == kwargs["interface_luid"])]
    return NO_ERROR
result = reconcile_after_crash(journal=RecoveryJournal(pathlib.Path(journal_path)),
    reservations=LabReservationRegistry(pathlib.Path(reservation_path)),
    read_table=read_table, read_snapshot=read_snapshot, delete=delete,
    environment_authority_granted=True,
    environment_id="synthetic-environment-0001",
    live_interface_guid="11111111-2222-4333-8444-555555555555",
    live_interface_index=58, live_interface_luid=0x3A00000000000000,
    now=datetime(2026, 8, 27, 12, 0, tzinfo=timezone.utc))
print(result.outcome, result.deletes_attempted, flush=True)
'''
    process = subprocess.Popen(
        [
            sys.executable,
            "-c",
            child,
            repo,
            str(journal.path),
            str(registry.path),
            str(ready),
            str(proceed),
            str(deleted),
        ],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    deadline = time.monotonic() + 10
    while not ready.exists() and process.poll() is None and time.monotonic() < deadline:
        time.sleep(0.02)
    assert ready.exists(), process.stderr.read() if process.poll() is not None else ""
    try:
        rival_world = World()
        rival = reconcile_after_crash(
            journal=journal,
            reservations=registry,
            read_table=rival_world.read_table,
            read_snapshot=rival_world.read_snapshot,
            delete=rival_world.delete,
            environment_authority_granted=True,
            environment_id=ENVIRONMENT,
            live_interface_guid=GUID,
            live_interface_index=IFINDEX,
            live_interface_luid=LUID,
            now=NOW,
        )
        assert rival.outcome == "BLOCKED"
        assert rival.deletes_attempted == 0
        assert rival_world.deletes == []
    finally:
        proceed.write_text("continue", encoding="utf-8")
    stdout, stderr = process.communicate(timeout=15)
    assert process.returncode == 0, stderr
    assert "RECONCILED 1" in stdout
    assert deleted.read_text(encoding="utf-8") == "one"


# --- reservation lifecycle after a crash ------------------------------------

def test_reconciliation_releases_only_the_crashed_operations_reservation(tmp_path):
    registry, reservation = _reservations(tmp_path)
    record = _record(reservation_id=reservation.reservation_id)
    world = World()
    result, _journal, registry = _reconcile(tmp_path, world, record, registry=registry)
    assert result.records[0].reservation_released is True
    assert registry.find(address=OWNED, environment_id=ENVIRONMENT, now=NOW) is None


def test_phase_b_reservation_write_failure_keeps_the_journal_open(tmp_path):
    registry, reservation = _reservations(tmp_path)
    real_release = registry.release

    def fail_release(*args, **kwargs):
        raise OSError("synthetic reservation write failure")

    registry.release = fail_release
    world = World()
    record = _record(reservation_id=reservation.reservation_id)
    result, journal, registry = _reconcile(
        tmp_path, world, record, registry=registry
    )
    registry.release = real_release
    assert result.outcome == "BLOCKED"
    assert result.deletes_attempted == 1
    assert len(journal.outstanding()) == 1
    assert registry.all()[0].is_released is False


def test_journal_close_failure_is_replayable_after_reservation_release(tmp_path):
    class OneFailedCloseJournal(RecoveryJournal):
        def __init__(self, path):
            super().__init__(path)
            self.fail_close = True

        def close(self, operation_id, reason):
            if self.fail_close:
                self.fail_close = False
                raise OSError("synthetic journal close failure")
            return super().close(operation_id, reason)

    journal = OneFailedCloseJournal(tmp_path / "journal.json")
    registry, reservation = _reservations(tmp_path)
    _persist_record(
        journal, _record(reservation_id=reservation.reservation_id)
    )
    world = World()
    first = reconcile_after_crash(
        journal=journal,
        reservations=registry,
        read_table=world.read_table,
        read_snapshot=world.read_snapshot,
        delete=world.delete,
        environment_authority_granted=True,
        environment_id=ENVIRONMENT,
        live_interface_guid=GUID,
        live_interface_index=IFINDEX,
        live_interface_luid=LUID,
        now=NOW,
    )
    assert first.outcome == "BLOCKED"
    assert first.deletes_attempted == 1
    assert len(journal.outstanding()) == 1
    assert registry.all()[0].is_released is True

    second = reconcile_after_crash(
        journal=journal,
        reservations=registry,
        read_table=world.read_table,
        read_snapshot=world.read_snapshot,
        delete=world.delete,
        environment_authority_granted=True,
        environment_id=ENVIRONMENT,
        live_interface_guid=GUID,
        live_interface_index=IFINDEX,
        live_interface_luid=LUID,
        now=NOW,
    )
    assert second.outcome == "ALREADY_ABSENT"
    assert second.deletes_attempted == 0
    assert journal.outstanding() == []


def test_a_reservation_bound_elsewhere_is_not_released(tmp_path):
    registry, reservation = _reservations(tmp_path, bind_to="a-different-operation")
    record = _record(reservation_id=reservation.reservation_id)
    world = World()
    result, _journal, registry = _reconcile(tmp_path, world, record, registry=registry)
    # The row was still ours to delete; the reservation was not ours to close.
    assert result.records[0].deleted is True
    assert result.records[0].reservation_released is False
    still = next(item for item in registry.all()
                 if item.reservation_id == reservation.reservation_id)
    assert still.is_released is False


def test_an_already_released_reservation_is_not_released_again(tmp_path):
    registry, reservation = _reservations(tmp_path, release=True)
    record = _record(reservation_id=reservation.reservation_id)
    world = World()
    result, _journal, _registry = _reconcile(tmp_path, world, record, registry=registry)
    assert result.records[0].reservation_released is False


def test_a_crashed_operations_reservation_never_authorises_a_new_one(tmp_path):
    """Cleanup must not become a way to inherit somebody else's authority."""
    from backend.recovery_lab.gate3 import evaluate_gate3_authority

    registry, reservation = _reservations(tmp_path)
    _found, assessment = evaluate_gate3_authority(
        candidate_address=OWNED, target_prefix="192.0.2.0/24",
        environment_id=ENVIRONMENT, run_id="a-brand-new-run",
        registry=registry, now=NOW,
    )
    assert assessment.usable is False
    assert "RESERVATION_BINDING_MISMATCH" in assessment.blockers


def test_reconciliation_never_reissues_or_rebinds_a_reservation(tmp_path):
    registry, reservation = _reservations(tmp_path)
    record = _record(reservation_id=reservation.reservation_id)
    world = World()
    before = len(registry.all())
    _result, _journal, registry = _reconcile(tmp_path, world, record, registry=registry)
    after = registry.all()
    assert len(after) == before
    assert after[0].operation_binding == OPERATION


def test_deleting_an_owned_row_does_not_require_a_live_reservation(tmp_path):
    """Reservation authority governs creating an address, not removing one.

    Requiring a live reservation here would mean an expired one could strand an
    address that is already outstanding.
    """
    registry, reservation = _reservations(tmp_path, release=True)
    record = _record(reservation_id=reservation.reservation_id)
    world = World()
    result, _journal, _registry = _reconcile(tmp_path, world, record, registry=registry)
    assert result.outcome == "RECONCILED"
    assert result.deletes_attempted == 1


def test_a_reservation_binding_cannot_be_overwritten(tmp_path):
    registry, reservation = _reservations(tmp_path, bind_to="first-operation")
    with pytest.raises(ReservationStateError):
        registry.bind(reservation.reservation_id, "second-operation")
    kept = registry.all()[0]
    assert kept.operation_binding == "first-operation"
    assert kept.is_released is False


def test_only_one_phase_a_claim_can_win_the_compare_and_set(tmp_path):
    registry, reservation = _reservations(tmp_path, bind_to=None)
    registry.claim(
        reservation.reservation_id,
        "first-operation",
        expected_binding=None,
        now=NOW,
    )
    with pytest.raises(ReservationStateError):
        registry.claim(
            reservation.reservation_id,
            "second-operation",
            expected_binding=None,
            now=NOW,
        )
    assert registry.all()[0].operation_binding == "first-operation"


def test_reservation_lifecycle_writes_refuse_missing_ids(tmp_path):
    registry = LabReservationRegistry(tmp_path / "reservations.json")
    with pytest.raises(ReservationStateError):
        registry.bind("missing-reservation", OPERATION)
    with pytest.raises(ReservationStateError):
        registry.release("missing-reservation", now=NOW)


def test_duplicate_live_reservations_are_ambiguous_not_selected(tmp_path):
    import json

    registry, _reservation = _reservations(tmp_path, bind_to=None)
    records = [dataclasses.asdict(item) for item in registry.all()]
    duplicate = dict(records[0])
    duplicate["reservation_id"] = "gate3-res-synthetic-duplicate"
    registry.path.write_text(
        json.dumps([records[0], duplicate]), encoding="utf-8"
    )
    with pytest.raises(ReservationStateError):
        registry.find(address=OWNED, environment_id=ENVIRONMENT, now=NOW)


# --- the deliberate crash ---------------------------------------------------

def test_the_crash_primitive_bypasses_python_cleanup():
    """`finally`, `atexit` and destructors must not run. Measured, not assumed."""
    import pathlib
    import subprocess
    import sys

    marker_dir = pathlib.Path(__file__).resolve().parent
    repo = str(marker_dir.parents[2])
    child = (
        "import sys, atexit;"
        "sys.path.insert(0, sys.argv[1]);"
        "from backend.recovery_lab.crash_experiment import _hard_exit;"
        "atexit.register(lambda: print('ATEXIT RAN'));"
        "print('before', flush=True);"
        "\ntry:\n"
        "    _hard_exit()\n"
        "finally:\n"
        "    print('FINALLY RAN', flush=True)\n"
    )
    completed = subprocess.run(
        [sys.executable, "-c", child, repo], capture_output=True, text=True
    )
    assert completed.returncode == 89
    assert "before" in completed.stdout
    assert "FINALLY RAN" not in completed.stdout
    assert "ATEXIT RAN" not in completed.stdout


def test_the_crash_exit_code_is_distinctive():
    from backend.recovery_lab.crash_experiment import CRASH_EXIT_CODE

    # Not 0, not 1, not 2: a shell must be able to tell "died where I told it"
    # from "failed for an ordinary reason".
    assert CRASH_EXIT_CODE not in (0, 1, 2)


@pytest.mark.parametrize(
    "address,ok",
    [
        ("192.0.2.250", True),
        ("198.51.100.5", True),
        ("203.0.113.9", True),
        ("192.168.99.5", False),
        ("10.0.0.1", False),
        ("198.18.0.101", False),
        ("not-an-address", False),
    ],
)
def test_only_documentation_space_may_be_left_behind(address, ok):
    from backend.recovery_lab.crash_experiment import candidate_is_documentation_space

    assert candidate_is_documentation_space(address) is ok


# --- Phase A composed end to end (without actually dying) -------------------

class CreateWorld(World):
    """Adds creation, so Phase A can be walked through in a test."""

    def __init__(self, rows=None, create_code=NO_ERROR, created_ts=CREATED_TS,
                 created_dad="PREFERRED"):
        super().__init__(rows if rows is not None else [_dhcp_primary()])
        self.create_code = create_code
        self.created_ts = created_ts
        self.created_dad = created_dad
        self.creates = 0

    def read_snapshot(self):
        snapshot = _snapshot()
        return dataclasses.replace(
            snapshot,
            interface_addresses=tuple(
                (row.address, row.prefix_length) for row in self.rows
            ),
        )

    def create(self, *, address, prefix_length, interface_index, interface_luid):
        self.creates += 1
        if self.create_code != NO_ERROR:
            return self.create_code
        self.rows.append(
            _row(address, prefix=prefix_length, index=interface_index,
                 luid=interface_luid, ts=self.created_ts, dad=self.created_dad)
        )
        return NO_ERROR


def _phase_a(tmp_path, world, *, registry=None, journal=None, **overrides):
    from backend.recovery_lab.crash_experiment import run_phase_a

    journal = journal or RecoveryJournal(tmp_path / "journal.json")
    if registry is None:
        registry = LabReservationRegistry(tmp_path / "reservations.json")
        registry.issue(
            address=OWNED, target_prefix="192.0.2.0/24", environment_id=ENVIRONMENT,
            attested_by="synthetic recovery lab harness",
            evidence_reference="crash-isolated-experiment", now=NOW,
        )
    kwargs = dict(
        interface_index=IFINDEX, interface_luid=LUID, interface_alias=ALIAS,
        interface_guid=GUID, candidate_address=OWNED,
        target_prefix="192.0.2.0/24", prefix_length=PREFIX,
        environment_id=ENVIRONMENT, environment_authority_granted=True,
        run_id="crash-run-0001", registry=registry, journal=journal,
        read_table=world.read_table, read_snapshot=world.read_snapshot,
        create=world.create, delete=world.delete, now=NOW, crash=False,
        sleep=lambda _s: None,
        dad_timeout=2.0,
    )
    kwargs.update(overrides)
    return run_phase_a(**kwargs), journal, registry


def test_phase_a_leaves_a_fully_evidenced_outstanding_claim(tmp_path):
    """The state Phase B has to recover from, built by the real Phase A path."""
    world = CreateWorld()
    result, journal, registry = _phase_a(tmp_path, world)

    assert result.outcome == "CRASHED_AS_INTENDED"
    assert result.creates_attempted == 1
    assert result.restored is False
    assert result.dad_state == "PREFERRED"

    outstanding = RecoveryJournal(tmp_path / "journal.json").outstanding()
    assert len(outstanding) == 1
    record = outstanding[0]
    assert record.state == "ADDRESS_CREATED"
    assert record.has_post_apply_evidence is True
    assert record.environment_id == ENVIRONMENT
    assert record.interface_guid == GUID
    assert record.reservation_id
    # The reservation is bound to the operation and still open.
    reservation = registry.all()[0]
    assert reservation.operation_binding == record.operation_id
    assert reservation.is_released is False


def test_the_two_phases_compose_into_a_full_recovery(tmp_path):
    """Phase A creates and abandons; a fresh Phase B proves and removes."""
    world = CreateWorld()
    result, _journal, registry = _phase_a(tmp_path, world)
    assert any(row.address == OWNED for row in world.rows)

    # The crash was suppressed so the test runner survives, which means this
    # process still holds the operation lock. Releasing it by hand stands in for
    # the kernel dropping it when the real Phase A process dies.
    result.operation_lock.release()

    # A brand new journal handle, as a new process would open.
    fresh = RecoveryJournal(tmp_path / "journal.json")
    reconciled = reconcile_after_crash(
        journal=fresh, reservations=registry, read_table=world.read_table,
        read_snapshot=world.read_snapshot, delete=world.delete,
        environment_authority_granted=True,
        environment_id=ENVIRONMENT, live_interface_guid=GUID,
        live_interface_index=IFINDEX, live_interface_luid=LUID, now=NOW,
    )
    assert reconciled.outcome == "RECONCILED"
    assert reconciled.deletes_attempted == 1
    assert reconciled.outstanding_after == 0
    assert not any(row.address == OWNED for row in world.rows)
    assert [row.address for row in world.rows] == [PRIMARY]
    assert registry.find(address=OWNED, environment_id=ENVIRONMENT, now=NOW) is None


def test_phase_a_records_intent_before_it_creates_anything(tmp_path):
    world = CreateWorld()
    result, _journal, _registry = _phase_a(tmp_path, world)
    names = [name for _status, name, _detail in result.steps]
    assert names.index("journal-intent") < names.index("create")
    assert names.index("create") < names.index("journal-created")
    assert names.index("operation-lock") < names.index("journal-intent")


def test_phase_a_refuses_without_a_reservation(tmp_path):
    registry = LabReservationRegistry(tmp_path / "reservations.json")
    world = CreateWorld()
    result, journal, _registry = _phase_a(tmp_path, world, registry=registry)
    assert result.outcome == "AUTHORITY_ABSENT"
    assert result.creates_attempted == 0
    assert world.creates == 0
    assert journal.outstanding() == []


def test_phase_a_refuses_on_an_unproven_environment(tmp_path):
    world = CreateWorld()
    result, journal, _registry = _phase_a(
        tmp_path, world, environment_authority_granted=False
    )
    assert result.outcome == "ENVIRONMENT_NOT_AUTHORISED"
    assert world.creates == 0
    assert journal.outstanding() == []


def test_phase_a_refuses_a_candidate_outside_documentation_space(tmp_path):
    world = CreateWorld()
    result, journal, _registry = _phase_a(
        tmp_path, world, candidate_address="192.168.99.99"
    )
    assert result.outcome == "CANDIDATE_NOT_DOCUMENTATION_SPACE"
    assert world.creates == 0
    assert journal.outstanding() == []


def test_phase_a_refuses_a_target_that_carries_a_default_route(tmp_path):
    world = CreateWorld()
    world.read_snapshot = lambda: _snapshot(
        default_routes=((IFINDEX, "198.18.0.1"),)
    )
    result, journal, registry = _phase_a(tmp_path, world)
    assert result.outcome == "INTERFACE_CARRIES_DEFAULT_ROUTE"
    assert result.creates_attempted == 0
    assert result.restored is True
    assert journal.outstanding() == []
    assert registry.all()[0].is_released is True


def test_baseline_read_failure_consumes_the_selected_reservation(tmp_path):
    world = CreateWorld()

    def unreadable_snapshot():
        raise OSError("synthetic snapshot failure")

    world.read_snapshot = unreadable_snapshot
    result, journal, registry = _phase_a(tmp_path, world)
    assert result.outcome == "BASELINE_INCOMPLETE"
    assert result.creates_attempted == 0
    assert result.restored is True
    assert journal.outstanding() == []
    assert registry.all()[0].is_released is True


def test_reservation_binding_race_means_zero_creates_and_no_overwrite(tmp_path):
    registry = LabReservationRegistry(tmp_path / "reservations.json")
    _outcome, reservation, _evidence = registry.issue(
        address=OWNED,
        target_prefix="192.0.2.0/24",
        environment_id=ENVIRONMENT,
        attested_by="synthetic recovery lab harness",
        evidence_reference="crash-isolated-experiment",
        now=NOW,
    )

    def lose_claim(reservation_id, operation_id, *, expected_binding, now):
        registry.bind(reservation_id, "rival-operation")
        raise ReservationStateError("synthetic compare-and-set loss")

    registry.claim = lose_claim
    world = CreateWorld()
    result, journal, registry = _phase_a(tmp_path, world, registry=registry)
    assert result.outcome == "RESERVATION_BINDING_FAILED"
    assert result.creates_attempted == 0
    assert result.restored is True
    assert journal.outstanding() == []
    assert registry.all()[0].operation_binding == "rival-operation"
    assert registry.all()[0].is_released is False


def test_binding_error_after_replace_releases_only_its_own_binding(tmp_path):
    registry = LabReservationRegistry(tmp_path / "reservations.json")
    registry.issue(
        address=OWNED,
        target_prefix="192.0.2.0/24",
        environment_id=ENVIRONMENT,
        attested_by="synthetic recovery lab harness",
        evidence_reference="crash-isolated-experiment",
        now=NOW,
    )
    real_claim = registry.claim

    def ambiguous_claim(*args, **kwargs):
        real_claim(*args, **kwargs)
        raise OSError("synthetic error after binding replace")

    registry.claim = ambiguous_claim
    world = CreateWorld()
    result, journal, registry = _phase_a(tmp_path, world, registry=registry)
    assert result.outcome == "RESERVATION_BINDING_FAILED"
    assert result.creates_attempted == 0
    assert result.restored is True
    assert journal.outstanding() == []
    assert registry.all()[0].operation_binding == result.operation_id
    assert registry.all()[0].is_released is True


def test_intent_persistence_failure_after_replace_is_safely_closed(tmp_path):
    class AmbiguousIntentJournal(RecoveryJournal):
        def record_intent(self, owned):
            super().record_intent(owned)
            raise OSError("synthetic error after replace")

    journal = AmbiguousIntentJournal(tmp_path / "journal.json")
    world = CreateWorld()
    result, journal, registry = _phase_a(tmp_path, world, journal=journal)
    assert result.outcome == "JOURNAL_PERSISTENCE_FAILURE"
    assert result.creates_attempted == 0
    assert result.restored is True
    assert journal.outstanding() == []
    assert journal.all()[0].state == "COMPLETED"
    assert registry.all()[0].is_released is True


def test_create_failure_closes_bookkeeping_only_after_proving_absence(tmp_path):
    world = CreateWorld(create_code=5)
    result, journal, registry = _phase_a(tmp_path, world)
    assert result.outcome == "ADDRESS_CREATE_FAILURE"
    assert result.creates_attempted == 1
    assert result.deletes_attempted == 0
    assert result.restored is True
    assert journal.outstanding() == []
    assert registry.all()[0].is_released is True


def test_reservation_persistence_failure_keeps_the_journal_link_open(tmp_path):
    registry = LabReservationRegistry(tmp_path / "reservations.json")
    registry.issue(
        address=OWNED,
        target_prefix="192.0.2.0/24",
        environment_id=ENVIRONMENT,
        attested_by="synthetic recovery lab harness",
        evidence_reference="crash-isolated-experiment",
        now=NOW,
    )
    original_release = registry.release

    def fail_release(*args, **kwargs):
        raise OSError("synthetic reservation persistence failure")

    registry.release = fail_release
    world = CreateWorld(create_code=5)
    result, journal, registry = _phase_a(tmp_path, world, registry=registry)
    registry.release = original_release
    assert result.outcome == "ADDRESS_CREATE_FAILURE"
    assert result.restored is True
    assert len(journal.outstanding()) == 1
    assert registry.all()[0].operation_binding == result.operation_id
    assert registry.all()[0].is_released is False


def test_post_apply_persistence_failure_uses_same_process_exact_rollback(tmp_path):
    class FailedCreatedJournal(RecoveryJournal):
        def record_created(self, *args, **kwargs):
            raise OSError("synthetic post-apply persistence failure")

    journal = FailedCreatedJournal(tmp_path / "journal.json")
    world = CreateWorld()
    result, journal, registry = _phase_a(tmp_path, world, journal=journal)
    assert result.outcome == "JOURNAL_PERSISTENCE_FAILURE"
    assert result.creates_attempted == 1
    assert result.deletes_attempted == 1
    assert result.restored is True
    assert journal.outstanding() == []
    assert registry.all()[0].is_released is True
    assert [row.address for row in world.rows] == [PRIMARY]


def test_phase_a_will_not_crash_on_an_unprovable_row(tmp_path):
    """No creation timestamp means a crash here would strand the address."""
    world = CreateWorld(created_ts=0)
    result, journal, _registry = _phase_a(tmp_path, world)
    assert result.outcome == "POST_APPLY_EVIDENCE_UNAVAILABLE"
    assert result.creates_attempted == 1
    assert result.deletes_attempted == 0
    # The claim stays outstanding so the operator is told, not surprised.
    assert len(journal.outstanding()) == 1
    assert _registry.all()[0].operation_binding == result.operation_id
    assert _registry.all()[0].is_released is False


def test_phase_a_will_not_crash_on_an_unsettled_row(tmp_path):
    world = CreateWorld(created_dad="TENTATIVE")
    result, journal, registry = _phase_a(tmp_path, world)
    assert result.outcome == "DAD_NOT_PREFERRED"
    assert result.deletes_attempted == 1
    assert result.restored is True
    assert journal.outstanding() == []
    assert registry.all()[0].is_released is True
    assert [row.address for row in world.rows] == [PRIMARY]


def test_phase_a_holds_the_operation_lock_until_it_dies(tmp_path):
    world = CreateWorld()
    result, journal, _registry = _phase_a(tmp_path, world)
    # Still held by this process: a reconciler must refuse while it lives.
    assert owner_process_is_gone(journal.operation_lock_dir, result.operation_id) is False


# --- product boundary -------------------------------------------------------

def test_no_mutation_primitive_is_reachable_from_the_product():
    import pathlib

    app = pathlib.Path(__file__).resolve().parents[1]
    offenders = []
    for path in app.rglob("*.py"):
        if "tests" in path.parts:
            continue
        source = path.read_text(encoding="utf-8")
        for forbidden in (
            "recovery_lab",
            "CreateUnicastIpAddressEntry",
            "DeleteUnicastIpAddressEntry",
            "New-NetIPAddress",
            "Remove-NetIPAddress",
            "os._exit",
        ):
            if forbidden in source:
                offenders.append(f"{path.name}: {forbidden}")
    assert offenders == []


def test_the_crash_primitive_lives_only_in_the_recovery_lab():
    import pathlib

    root = pathlib.Path(__file__).resolve().parents[2]
    hits = {
        path.parts[path.parts.index("backend") + 1]
        for path in root.rglob("*.py")
        if "backend" in path.parts
        and "tests" not in path.parts
        and "_hard_exit" in path.read_text(encoding="utf-8")
    }
    assert hits == {"recovery_lab"}


def test_the_planner_remains_planning_only():
    from app.recovery_execution import build_planning_architecture

    architecture = build_planning_architecture(plan_status="BLOCKED", blocker_codes=())
    assert architecture.mode == "PLANNING_ONLY"
    assert architecture.executor_implemented is False
    assert architecture.approval_available is False
    assert architecture.primitive.selected_primitive == "NONE"


def test_a_production_plan_without_a_reservation_remains_blocked():
    from app.recovery_execution import assess_recovery_reservation

    result = assess_recovery_reservation(
        None, candidate_address=OWNED, management_prefix="192.0.2.0/24",
        target_address="192.0.2.10", gateway_address="192.0.2.1",
        local_addresses=[], now=NOW,
    )
    assert result.usable is False
    assert result.blockers == ["NO_RESERVATION"]


# --- capability semantics ---------------------------------------------------

def test_the_capability_is_validated_by_the_run_and_not_by_this_suite():
    """Code and tests are still not a measurement; a real run is.

    Everything above this line is adversarial simulation. None of it may be what
    validates the capability, so the record has to be anchored to an observation
    taken somewhere -- a named environment and a time -- rather than to the fact
    that these tests pass. What that run measured is asserted separately, in
    `test_crash_ownership_measured_evidence`.
    """
    from app.recovery_capability import current_capability_state

    entry = next(
        item
        for item in current_capability_state().capabilities
        if item.capability == "CRASH_OWNERSHIP_RECONCILIATION"
    )
    assert entry.status == "VALIDATED"
    assert entry.environment == "DISPOSABLE_DHCP_ADAPTER"
    assert entry.observed_at is not None


def test_the_production_adapter_class_remains_unmeasured():
    from app.recovery_capability import current_capability_state

    entry = next(
        item
        for item in current_capability_state().capabilities
        if item.capability == "PRODUCTION_ADAPTER_CLASS"
    )
    assert entry.status == "NOT_ATTEMPTED"
    assert entry.environment == "NONE"


def test_production_recovery_remains_unvalidated():
    from app.recovery_capability import current_capability_state

    state = current_capability_state()
    assert state.production_recovery_validated is False
    # Crash ownership is no longer one of the reasons. One is left, and no
    # experiment on a disposable adapter can ever retire it.
    assert state.unvalidated_for_production == ["PRODUCTION_ADAPTER_CLASS"]


def test_the_successful_crash_validation_alone_did_not_validate_production():
    """Looking back: the successful crash experiment was not enough.

    This was written as a lookahead before the experiment ran. It now describes
    what happened: the patch below is a no-op against the live record, and
    production recovery stayed False anyway.
    """
    from app.recovery_capability import build_capability_state, current_capability_state

    patched = [
        item.model_copy(
            update={"status": "VALIDATED", "environment": "DISPOSABLE_DHCP_ADAPTER"}
        )
        if item.capability == "CRASH_OWNERSHIP_RECONCILIATION"
        else item
        for item in current_capability_state().capabilities
    ]
    result = build_capability_state(patched)
    assert result.production_recovery_validated is False
    assert "PRODUCTION_ADAPTER_CLASS" in result.unvalidated_for_production


# --- privacy ----------------------------------------------------------------

def test_the_fixtures_carry_no_real_machine_values():
    import ipaddress
    import re

    for guid in (GUID, OTHER_GUID):
        # Synthetic by construction: repeated digit groups, not a real GUID.
        assert re.fullmatch(r"[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-8[0-9a-f]{3}-[0-9a-f]{12}", guid)
        assert len(set(guid.replace("-", ""))) <= 6

    import getpass

    # Read the account name rather than hard-coding one: spelling a real
    # username here to assert its absence would be the leak it checks for.
    account = getpass.getuser()
    for name in (ENVIRONMENT, OTHER_ENVIRONMENT, OPERATION, RESERVATION, ALIAS):
        assert "recovery-env-" not in name or "synthetic" in name
        assert not re.search(r"[A-Za-z]:\\", name)
        assert account.lower() not in name.lower()

    documentation = ipaddress.ip_network("192.0.2.0/24")
    benchmark = ipaddress.ip_network("198.18.0.0/15")
    assert ipaddress.ip_address(OWNED) in documentation
    assert ipaddress.ip_address(PRIMARY) in benchmark


def test_no_real_lease_address_appears_in_the_fixtures():
    # The values, not the file: scanning the source would trip over the very
    # patterns it is looking for. Only the documentation-space parametrisation
    # names a private address, and only as a rejected counterexample.
    import ipaddress

    for value in (OWNED, PRIMARY):
        parsed = ipaddress.ip_address(value)
        assert (
            parsed in ipaddress.ip_network("192.0.2.0/24")
            or parsed in ipaddress.ip_network("198.18.0.0/15")
        ), value
    for name in (ENVIRONMENT, OTHER_ENVIRONMENT, OPERATION, RESERVATION, GUID):
        assert "synthetic" in name or set(name.replace("-", "")) <= set("0123456789abcdef")
