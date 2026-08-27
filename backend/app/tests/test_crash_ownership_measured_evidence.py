"""What the real crash-ownership experiment measured, and what it did not.

The two-process experiment has now been run, elevated, on the harness-owned
disposable DHCP environment. One process created a temporary RFC 5737 address
and then died through the crash path without cleaning up; a second, unrelated
process reconstructed ownership from durable state alone and removed exactly
that row, leaving the DHCP primary and the surrounding network baseline as it
found them.

That is a real result and it is a narrow one. It was measured inside a single
Windows boot, on a virtual adapter this harness created and owns. It says
nothing about a reboot, a machine crash, a power loss, a NIC or driver reset, an
adapter that was recreated underneath the record, or any production interface.

So, as with every gate before it, most of this file asserts what stayed false.
`PRODUCTION_ADAPTER_CLASS` is now the *only* capability holding
`production_recovery_validated` at False, which makes the pressure on it higher
than it has ever been -- and the tests here correspondingly blunter about the
fact that nothing measured on a disposable adapter can ever satisfy it.
"""
from __future__ import annotations

import re
from datetime import datetime, timezone
from pathlib import Path

import pytest

from app.recovery_capability import (
    PRODUCTION_REQUIRED_CAPABILITIES,
    build_capability_state,
    current_capability_state,
)
from app.recovery_execution import (
    assess_recovery_reservation,
    build_planning_architecture,
)

NOW = datetime(2026, 8, 27, 12, 0, tzinfo=timezone.utc)

#: Tracked files that carry the experiment's claims in prose. The run's own
#: durable records live in the gitignored lab state directory and must stay
#: there; these are the files a leak would actually reach.
EVIDENCE_SOURCES = (
    Path(__file__).resolve().parents[1] / "recovery_capability.py",
    Path(__file__).resolve().parents[2] / "recovery_lab" / "README.md",
)


def _capability(name: str):
    return next(
        item
        for item in current_capability_state().capabilities
        if item.capability == name
    )


# --- the measured result ---------------------------------------------------

def test_crash_ownership_is_validated_on_a_disposable_dhcp_adapter():
    entry = _capability("CRASH_OWNERSHIP_RECONCILIATION")
    assert entry.status == "VALIDATED"
    assert entry.environment == "DISPOSABLE_DHCP_ADAPTER"
    assert entry.observed_at is not None


def test_the_evidence_records_that_the_first_process_really_died():
    # The whole gate depends on cleanup *not* running. A tidy shutdown that
    # happened to leave an address behind would prove nothing.
    detail = _capability("CRASH_OWNERSHIP_RECONCILIATION").detail
    for claim in ("crash path", "exit code 89", "no rollback"):
        assert claim in detail, claim


def test_the_evidence_records_that_the_row_outlived_its_creator():
    # The observation that makes this a crash experiment rather than a delete
    # test: both rows present at once, the temporary one with no live owner.
    detail = _capability("CRASH_OWNERSHIP_RECONCILIATION").detail
    for claim in ("both rows", "outlived its creator", "192.0.2.251"):
        assert claim in detail, claim


def test_the_evidence_records_reconstruction_by_a_second_process():
    detail = _capability("CRASH_OWNERSHIP_RECONCILIATION").detail
    for claim in (
        "second, unrelated process",
        "durable state alone",
        "DELETE_AUTHORISED",
        "exactly once",
        "zero outstanding",
    ):
        assert claim in detail, claim


def test_the_evidence_records_what_survived_the_reconciliation():
    # Deleting the row is half the claim; leaving everything else alone is the
    # half that makes it safe.
    detail = _capability("CRASH_OWNERSHIP_RECONCILIATION").detail
    for claim in ("DHCP primary", "addressing and network", "baselines intact"):
        assert claim in detail, claim


def test_the_creation_timestamp_claim_stays_narrow():
    """CreationTimeStamp is one predicate among several, not row identity.

    It is a same-boot discriminator: useful for telling a recreated row from the
    one we made, worthless across a reboot, and never a cryptographic or
    permanent object id. The evidence must not overstate it.
    """
    detail = _capability("CRASH_OWNERSHIP_RECONCILIATION").detail
    assert "CreationTimeStamp" in detail
    # Named as one of the predicates that had to match, alongside the adapter
    # GUID, LUID and index -- not as the thing that established ownership.
    assert "every ownership predicate" in detail
    for overclaim in ("unique", "cryptographic", "permanent", "identifier for"):
        assert overclaim not in detail, overclaim


# --- what was deliberately not measured ------------------------------------

def test_the_evidence_scopes_itself_to_a_single_boot():
    detail = _capability("CRASH_OWNERSHIP_RECONCILIATION").detail
    assert "single Windows boot" in detail
    assert "Same boot only" in detail


@pytest.mark.parametrize(
    "unmeasured",
    ["reboot", "machine crash", "power loss", "NIC reset", "driver restart",
     "adapter recreation"],
)
def test_the_evidence_names_each_thing_it_does_not_cover(unmeasured):
    # Listed explicitly rather than left to inference: a reader who skims this
    # record should not be able to come away thinking it covers a reboot.
    detail = _capability("CRASH_OWNERSHIP_RECONCILIATION").detail
    assert unmeasured in detail


def test_the_evidence_disclaims_the_production_adapter():
    detail = _capability("CRASH_OWNERSHIP_RECONCILIATION").detail
    assert "not a production adapter" in detail
    assert _capability("CRASH_OWNERSHIP_RECONCILIATION").environment != (
        "PRODUCTION_ADAPTER"
    )


# --- the run's identity stayed in the lab ----------------------------------

def test_the_evidence_carries_no_machine_specific_values():
    detail = _capability("CRASH_OWNERSHIP_RECONCILIATION").detail
    # No interface GUID, environment id, reservation id, operation id, lease
    # address, username, or local path.
    assert re.search(r"[0-9a-f]{8}-[0-9a-f]{4}", detail) is None
    assert "recovery-env-" not in detail
    assert "gate3-res-" not in detail
    assert "recovery-op-" not in detail
    assert re.search(r"192\.168\.\d+\.\d+", detail) is None
    assert re.search(r"[A-Za-z]:\\", detail) is None


#: A real run id is a prefix plus machine-generated hex. The fixtures spell
#: theirs "synthetic" on purpose, so the shape alone separates them.
_RUN_ID_SHAPES = (
    r"recovery-env-[0-9a-f]{10,}",
    r"gate3-res-[0-9a-f]{10,}",
    r"recovery-op-[0-9a-f]{10,}",
)


@pytest.mark.parametrize("source", EVIDENCE_SOURCES, ids=lambda p: p.name)
def test_no_run_scoped_identifier_reached_a_tracked_file(source):
    """The durable records stay in the gitignored state directory.

    Checked by shape rather than by value: asserting the real ids are absent
    would mean committing them here to compare against, which is the leak.
    """
    text = source.read_text(encoding="utf-8")
    for shape in _RUN_ID_SHAPES:
        assert re.search(shape, text) is None, f"{source.name}: {shape}"
    for guid in re.findall(
        r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}", text
    ):
        # Synthetic by construction, the same rule the crash fixtures use.
        assert len(set(guid.replace("-", ""))) <= 6, f"{source.name}: {guid}"
    assert re.search(r"[A-Za-z]:\\Users\\", text) is None, source.name


def test_the_capability_record_names_no_private_address():
    # This module is prose about evidence. Documentation-space addresses may
    # appear; a real lease or host address never should.
    source = (Path(__file__).resolve().parents[1] / "recovery_capability.py").read_text(
        encoding="utf-8"
    )
    for private in (r"192\.168\.\d+\.\d+", r"10\.\d+\.\d+\.\d+", r"172\.(1[6-9]|2\d|3[01])\."):
        assert re.search(private, source) is None, private


# --- the earlier gates are untouched ---------------------------------------

@pytest.mark.parametrize(
    "capability",
    [
        "EPHEMERAL_ADDRESS_CREATE",
        "DUPLICATE_ADDRESS_DETECTION",
        "EXPLICIT_ON_LINK_PREFIX",
        "EXACT_ADDRESS_DELETE",
        "ROLLBACK_RESTORES_BASELINE",
    ],
)
def test_gate_one_remains_validated_on_the_isolated_static_adapter(capability):
    entry = _capability(capability)
    assert entry.status == "VALIDATED"
    assert entry.environment == "ISOLATED_STATIC_ADAPTER"


@pytest.mark.parametrize(
    "capability",
    ["DHCP_SAME_INTERFACE_COEXISTENCE", "COLLISION_SAFE_ADDRESS_AUTHORITY"],
)
def test_gates_two_and_three_remain_validated_where_they_were_measured(capability):
    entry = _capability(capability)
    assert entry.status == "VALIDATED"
    assert entry.environment == "DISPOSABLE_DHCP_ADAPTER"


# --- what stayed false -----------------------------------------------------

def test_no_experiment_has_run_on_a_production_adapter():
    entry = _capability("PRODUCTION_ADAPTER_CLASS")
    assert entry.status == "NOT_ATTEMPTED"
    assert entry.environment == "NONE"
    assert entry.observed_at is None
    assert all(
        item.environment != "PRODUCTION_ADAPTER"
        for item in current_capability_state().capabilities
    )


def test_production_recovery_is_still_not_validated():
    state = current_capability_state()
    assert state.production_recovery_validated is False
    # One reason left, and it is the one no lab experiment can retire.
    assert state.unvalidated_for_production == ["PRODUCTION_ADAPTER_CLASS"]


def test_no_production_executor_or_approval_path_became_available():
    architecture = build_planning_architecture(plan_status="BLOCKED", blocker_codes=())
    assert architecture.mode == "PLANNING_ONLY"
    assert architecture.executor_implemented is False
    assert architecture.approval_available is False
    assert architecture.primitive.selected_primitive == "NONE"


def test_a_production_plan_without_a_reservation_is_still_blocked():
    # Crash ownership answers "can we clean up after ourselves?". It does not
    # produce an address anyone has authority to use.
    result = assess_recovery_reservation(
        None,
        candidate_address="192.0.2.250",
        management_prefix="192.0.2.0/24",
        target_address="192.0.2.10",
        gateway_address="192.0.2.1",
        local_addresses=[],
        now=NOW,
    )
    assert result.usable is False
    assert result.blockers == ["NO_RESERVATION"]


# --- provenance: the timestamp came from the run ---------------------------

#: The gates whose observation time was read back from a durable record the run
#: itself wrote. Everything else predates the lab keeping one.
MACHINE_RECORDED = ("COLLISION_SAFE_ADDRESS_AUTHORITY", "CRASH_OWNERSHIP_RECONCILIATION")


@pytest.mark.parametrize("capability", MACHINE_RECORDED)
def test_a_machine_recorded_timestamp_carries_sub_second_precision(capability):
    """A hand-typed time lands on a round second; a recorded one does not.

    A smell test rather than a proof, but it is the smell that was actually
    missed once: `12:00:00.000000` came from a keyboard.
    """
    entry = _capability(capability)
    assert entry.observed_at is not None
    assert (entry.observed_at.second, entry.observed_at.microsecond) != (0, 0)
    assert entry.observed_at.microsecond != 0


def test_the_crash_observation_follows_the_gate_three_observation():
    # Ordering that the run history fixes: Gate 3 was measured first, and the
    # crash experiment reused the same environment afterwards. A timestamp
    # invented later would have no reason to respect that.
    crash = _capability("CRASH_OWNERSHIP_RECONCILIATION").observed_at
    authority = _capability("COLLISION_SAFE_ADDRESS_AUTHORITY").observed_at
    assert crash is not None and authority is not None
    assert crash > authority


def test_no_capability_claims_to_have_been_observed_in_the_future():
    now = datetime.now(timezone.utc)
    for item in current_capability_state().capabilities:
        if item.observed_at is not None:
            assert item.observed_at <= now, item.capability


def test_a_validated_capability_says_when_and_an_unattempted_one_does_not():
    for item in current_capability_state().capabilities:
        if item.status == "VALIDATED":
            assert item.observed_at is not None, item.capability
        if item.status == "NOT_ATTEMPTED":
            assert item.observed_at is None, item.capability


# --- production recovery must not outrun production evidence ---------------

def test_validating_crash_ownership_did_not_validate_production_recovery():
    """The regression the capability audit was built for, now observed for real.

    Before `PRODUCTION_ADAPTER_CLASS` existed, crash ownership was the last
    unvalidated production-required capability. Recording this experiment would
    have flipped `production_recovery_validated` to True on the strength of a
    virtual adapter. It did not.
    """
    state = current_capability_state()
    assert _capability("CRASH_OWNERSHIP_RECONCILIATION").status == "VALIDATED"
    assert state.production_recovery_validated is False
    assert _capability("PRODUCTION_ADAPTER_CLASS").status == "NOT_ATTEMPTED"


def test_the_production_capability_is_the_only_thing_left_and_cannot_be_faked():
    """Validate everything a lab can validate; production stays False."""
    everything_but_production = [
        item.model_copy(
            update={"status": "VALIDATED", "environment": "DISPOSABLE_DHCP_ADAPTER",
                    "observedAt": NOW}
        )
        if item.capability != "PRODUCTION_ADAPTER_CLASS"
        else item
        for item in current_capability_state().capabilities
    ]
    result = build_capability_state(everything_but_production)
    assert result.production_recovery_validated is False
    assert result.unvalidated_for_production == ["PRODUCTION_ADAPTER_CLASS"]


def test_production_adapter_class_is_still_a_production_prerequisite():
    assert "PRODUCTION_ADAPTER_CLASS" in PRODUCTION_REQUIRED_CAPABILITIES


def test_the_primitive_verdict_did_not_move():
    # `primitive_validated` was already True on the create/DAD/delete core.
    # Crash ownership is about the bookkeeping around it, not the call itself.
    assert current_capability_state().primitive_validated is True
