"""The isolated Gate 3 experiment: authority first, mutation second.

The single most important number in this file is ``creates_attempted``. Every
refusal path must leave it at zero, because a Gate 3 refusal that had already
created an address would have proved the opposite of what the gate claims.

The second most important property is composition: reservation authority must
not quietly grant environment ownership, environment ownership must not grant
reservation authority, and neither may substitute for duplicate address
detection or for exact-row rollback.

Everything here is synthetic. The DHCP primary uses RFC 2544 benchmarking space
rather than any real lab subnet, and the candidate uses RFC 5737 documentation
space.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from backend.recovery_lab.gate3 import (
    _BLOCKER_OUTCOME,
    Gate3Outcome,
    evaluate_gate3_authority,
    run_gate3_experiment,
)
from backend.recovery_lab.journal import RecoveryJournal
from backend.recovery_lab.reservation import (
    DEFAULT_VALIDITY,
    LAB_AUTHORITY,
    SCHEMA_VERSION,
    LabReservationRegistry,
    to_product_reservation,
)
from backend.recovery_lab.windows_unicast import NO_ERROR, UnicastAddress

NOW = datetime(2026, 8, 27, 12, 0, tzinfo=timezone.utc)
IFINDEX = 58
LUID = 0x3A00000000000000
ALIAS = "synthetic-lab-adapter"
ENVIRONMENT = "synthetic-environment-0001"
RUN = "gate3-run-0001"

PRIMARY = "198.18.0.101"        # RFC 2544 benchmarking space, DHCP-served
CANDIDATE = "192.0.2.250"       # RFC 5737 documentation space
PREFIX = "192.0.2.0/24"


def _row(address, *, origin=("DHCP", "DHCP"), dad="PREFERRED", lifetime=3600,
         prefix=24):
    return UnicastAddress(
        address=address, prefix_length=prefix, interface_index=IFINDEX,
        interface_luid=LUID, prefix_origin=origin[0], suffix_origin=origin[1],
        dad_state=dad, valid_lifetime=lifetime, preferred_lifetime=lifetime,
        skip_as_source=False,
    )


def _manual(address, **kw):
    kw.setdefault("origin", ("MANUAL", "MANUAL"))
    kw.setdefault("lifetime", 0xFFFFFFFF)
    return _row(address, **kw)


class _Snapshot:
    """Just enough surrounding state for the delegated Gate 2 evaluator."""

    def __init__(self, addresses, routes):
        from backend.recovery_lab.coexistence import NetworkSnapshot

        self.value = NetworkSnapshot(
            interface_addresses=tuple(addresses),
            interface_routes=tuple(routes),
            default_routes=((16, "198.18.0.1"),),
            dns_servers=("198.18.0.53",),
        )


class World:
    """A fake interface whose address table responds to create and delete."""

    def __init__(self, *, created_dad="PREFERRED"):
        self.rows = [_row(PRIMARY)]
        self.created_dad = created_dad
        self.creates = 0
        self.deletes: list[dict] = []
        self._created = False

    def read_table(self):
        return list(self.rows)

    def read_snapshot(self):
        routes = ["198.18.0.0/24"] + ([PREFIX] if self._created else [])
        return _Snapshot(
            [(row.address, row.prefix_length) for row in self.rows], routes
        ).value

    def create(self, *, address, prefix_length, interface_index, interface_luid):
        self.creates += 1
        self.rows.append(_manual(address, dad=self.created_dad))
        self._created = True
        return NO_ERROR

    def delete(self, *, address, prefix_length, interface_index, interface_luid):
        self.deletes.append({"address": address, "prefix_length": prefix_length})
        self.rows = [row for row in self.rows if row.address != address]
        self._created = False
        return NO_ERROR


@pytest.fixture()
def registry(tmp_path):
    return LabReservationRegistry(tmp_path / "gate3-reservations.json")


def _issue(registry, *, address=CANDIDATE, environment=ENVIRONMENT, now=NOW,
           **overrides):
    kwargs = {
        "address": address,
        "target_prefix": PREFIX,
        "environment_id": environment,
        "attested_by": "synthetic recovery lab harness",
        "evidence_reference": "gate3-isolated-experiment",
        "now": now,
    }
    kwargs.update(overrides)
    return registry.issue(**kwargs)


def _run(tmp_path, registry, world, *, granted=True, environment=ENVIRONMENT,
         run_id=RUN, address=CANDIDATE, now=NOW):
    return run_gate3_experiment(
        interface_index=IFINDEX,
        interface_luid=LUID,
        interface_alias=ALIAS,
        candidate_address=address,
        target_prefix=PREFIX,
        prefix_length=24,
        environment_id=environment,
        environment_authority_granted=granted,
        run_id=run_id,
        registry=registry,
        journal=RecoveryJournal(tmp_path / "journal.json"),
        read_table=world.read_table,
        read_snapshot=world.read_snapshot,
        create=world.create,
        delete=world.delete,
        now=now,
        sleep=lambda _s: None,
        dad_timeout=2.0,
    )


# --- the reservation registry ----------------------------------------------

def test_a_reservation_is_a_positive_claim_about_one_address(registry):
    outcome, reservation, evidence = _issue(registry)
    assert outcome == "ISSUED"
    assert reservation is not None
    assert reservation.address == CANDIDATE
    assert reservation.environment_id == ENVIRONMENT
    assert reservation.authority == LAB_AUTHORITY
    assert reservation.schema_version == SCHEMA_VERSION
    # It says until when, rather than leaving freshness to be inferred.
    until = datetime.fromisoformat(reservation.reserved_until)
    assert until == NOW + DEFAULT_VALIDITY


def test_the_lab_will_not_reserve_an_address_it_has_no_standing_over(registry):
    # Reserving a real private address would be a claim about somebody's network.
    outcome, reservation, _ = _issue(registry, address="10.10.10.10",
                                     target_prefix="10.10.10.0/24")
    assert outcome == "CANDIDATE_OUTSIDE_DOCUMENTATION_SPACE"
    assert reservation is None


@pytest.mark.parametrize("address", ["192.0.2.0", "192.0.2.255"])
def test_structurally_unsafe_addresses_are_never_reserved(registry, address):
    outcome, reservation, _ = _issue(registry, address=address)
    assert outcome == "CANDIDATE_STRUCTURALLY_UNSAFE"
    assert reservation is None


def test_the_gateway_is_never_reserved(registry):
    outcome, _, _ = _issue(registry, address="192.0.2.1", gateway="192.0.2.1")
    assert outcome == "CANDIDATE_STRUCTURALLY_UNSAFE"


def test_two_live_reservations_for_one_address_are_refused(registry):
    assert _issue(registry)[0] == "ISSUED"
    outcome, reservation, _ = _issue(registry)
    assert outcome == "CANDIDATE_ALREADY_RESERVED"
    assert reservation is None


def test_a_reservation_must_name_its_environment(registry):
    outcome, _, _ = _issue(registry, environment="")
    assert outcome == "ENVIRONMENT_NOT_NAMED"


def test_an_expired_reservation_is_not_returned_as_authority(registry):
    _issue(registry, validity=timedelta(minutes=5))
    later = NOW + timedelta(minutes=6)
    assert registry.find(address=CANDIDATE, environment_id=ENVIRONMENT,
                         now=later) is None


def test_a_released_reservation_is_not_returned_as_authority(registry):
    _, reservation, _ = _issue(registry)
    registry.release(reservation.reservation_id, now=NOW)
    assert registry.find(address=CANDIDATE, environment_id=ENVIRONMENT,
                         now=NOW) is None
    # The record itself is kept, so what was authorised stays auditable.
    assert len(registry.all()) == 1


def test_a_registry_from_an_unknown_schema_is_refused_not_reinterpreted(tmp_path):
    path = tmp_path / "gate3-reservations.json"
    path.write_text('[{"schema_version": 99, "address": "192.0.2.250"}]',
                    encoding="utf-8")
    with pytest.raises(RuntimeError):
        LabReservationRegistry(path).all()


def test_an_unreadable_registry_is_refused_not_treated_as_empty(tmp_path):
    # An empty registry means "refuse", so a corrupt one that read as empty
    # would refuse for the wrong reason and hide a real problem.
    path = tmp_path / "gate3-reservations.json"
    path.write_text("{not json", encoding="utf-8")
    with pytest.raises(RuntimeError):
        LabReservationRegistry(path).all()


def test_a_lab_record_is_rendered_in_the_product_schema(registry):
    _, reservation, _ = _issue(registry)
    payload = to_product_reservation(reservation)
    assert payload["scope"] == "DISPOSABLE_LAB_ENVIRONMENT"
    assert payload["authority"] == "LAB_HARNESS_RESERVED"
    assert payload["attestorType"] == "LAB_HARNESS"
    assert payload["networkScopeId"] == ENVIRONMENT


# --- refusals: every one of these must create nothing ----------------------

def test_no_reservation_means_zero_creates(tmp_path, registry):
    world = World()
    result = _run(tmp_path, registry, world)
    assert result.outcome == "AUTHORITY_ABSENT"
    assert result.creates_attempted == 0
    assert world.creates == 0
    assert result.restored is True


def test_an_expired_reservation_means_zero_creates(tmp_path, registry):
    _issue(registry, validity=timedelta(minutes=5))
    world = World()
    result = _run(tmp_path, registry, world, now=NOW + timedelta(minutes=6))
    # An expired record is indistinguishable from no record: both are no claim.
    assert result.outcome == "AUTHORITY_ABSENT"
    assert world.creates == 0


def test_a_reservation_for_another_address_means_zero_creates(tmp_path, registry):
    _issue(registry, address="192.0.2.251")
    world = World()
    result = _run(tmp_path, registry, world, address=CANDIDATE)
    assert result.outcome == "AUTHORITY_ABSENT"
    assert world.creates == 0


def test_a_reservation_from_another_environment_means_zero_creates(tmp_path, registry):
    _issue(registry, environment="a-different-synthetic-environment")
    world = World()
    result = _run(tmp_path, registry, world, environment=ENVIRONMENT)
    assert result.outcome == "AUTHORITY_ABSENT"
    assert world.creates == 0


def test_an_unproven_environment_means_zero_creates(tmp_path, registry):
    _issue(registry)
    world = World()
    result = _run(tmp_path, registry, world, granted=False)
    assert result.outcome == "ENVIRONMENT_NOT_AUTHORISED"
    assert world.creates == 0
    assert result.restored is True


def test_a_reservation_does_not_make_an_interface_ours(tmp_path, registry):
    # A perfectly valid reservation plus an unproven environment is still a
    # refusal: the two authorities answer different questions.
    _issue(registry)
    world = World()
    result = _run(tmp_path, registry, world, granted=False)
    assert result.outcome == "ENVIRONMENT_NOT_AUTHORISED"
    assert world.creates == 0


def test_an_address_the_host_already_holds_means_zero_creates(tmp_path, registry):
    _issue(registry)
    world = World()
    world.rows.append(_manual(CANDIDATE))
    result = _run(tmp_path, registry, world)
    assert result.outcome == "CANDIDATE_STRUCTURALLY_UNSAFE"
    assert world.creates == 0


def test_an_address_another_operation_owns_means_zero_creates(tmp_path, registry):
    from backend.recovery_lab.journal import OwnedAddress, now_iso

    _issue(registry)
    journal = RecoveryJournal(tmp_path / "journal.json")
    journal.record_intent(
        OwnedAddress(
            operation_id="a-previous-operation", plan_id="previous",
            interface_alias=ALIAS, interface_index=IFINDEX, interface_luid=LUID,
            address=CANDIDATE, prefix_length=24, created_at=now_iso(),
            state="INTENT_RECORDED", previous_state_fingerprint="",
        )
    )
    world = World()
    result = run_gate3_experiment(
        interface_index=IFINDEX, interface_luid=LUID, interface_alias=ALIAS,
        candidate_address=CANDIDATE, target_prefix=PREFIX, prefix_length=24,
        environment_id=ENVIRONMENT, environment_authority_granted=True,
        run_id=RUN, registry=registry, journal=journal,
        read_table=world.read_table, read_snapshot=world.read_snapshot,
        create=world.create, delete=world.delete, now=NOW,
        sleep=lambda _s: None, dad_timeout=2.0,
    )
    assert result.outcome == "CANDIDATE_STRUCTURALLY_UNSAFE"
    assert world.creates == 0


def test_every_declared_blocker_maps_to_a_refusal_outcome():
    import typing

    from backend.app.recovery_execution import ReservationBlocker

    declared = set(typing.get_args(ReservationBlocker))
    assert declared == set(_BLOCKER_OUTCOME)
    assert set(_BLOCKER_OUTCOME.values()) <= set(typing.get_args(Gate3Outcome))


# --- crash and replay ------------------------------------------------------

def test_a_reservation_bound_to_a_dead_run_does_not_authorise_a_new_one(
    tmp_path, registry
):
    # This is the crash case: a run claimed the reservation, then died. The
    # record is still live and still names the address, but it belongs to an
    # operation that is over.
    _, reservation, _ = _issue(registry)
    registry.bind(reservation.reservation_id, "gate3-run-that-crashed")

    world = World()
    result = _run(tmp_path, registry, world, run_id="gate3-run-0002")
    assert result.outcome == "AUTHORITY_SCOPE_MISMATCH"
    assert "RESERVATION_BINDING_MISMATCH" in result.authority_blockers
    assert world.creates == 0


def test_a_completed_run_releases_its_reservation(tmp_path, registry):
    _issue(registry)
    world = World()
    result = _run(tmp_path, registry, world)
    assert result.outcome == "SUCCESS"
    assert registry.find(address=CANDIDATE, environment_id=ENVIRONMENT,
                         now=NOW) is None


def test_a_second_run_cannot_reuse_a_released_reservation(tmp_path, registry):
    _issue(registry)
    assert _run(tmp_path, registry, World()).outcome == "SUCCESS"
    second = _run(tmp_path, registry, World(), run_id="gate3-run-0002")
    assert second.outcome == "AUTHORITY_ABSENT"


def test_a_failed_run_also_releases_its_reservation(tmp_path, registry):
    _issue(registry)
    world = World(created_dad="DUPLICATE")
    result = _run(tmp_path, registry, world)
    assert result.outcome == "AUTHORITY_CONTRADICTED_BY_DAD"
    assert registry.find(address=CANDIDATE, environment_id=ENVIRONMENT,
                         now=NOW) is None


# --- authority never substitutes for DAD or for rollback -------------------

def test_a_reserved_address_reported_duplicate_is_a_contradiction_not_a_pass(
    tmp_path, registry
):
    _issue(registry)
    world = World(created_dad="DUPLICATE")
    result = _run(tmp_path, registry, world)
    # The reservation said yes; the wire said no. The wire wins.
    assert result.outcome == "AUTHORITY_CONTRADICTED_BY_DAD"
    assert result.dad_state == "DUPLICATE"
    assert result.restored is True


def test_a_duplicate_does_not_cause_another_address_to_be_tried(tmp_path, registry):
    _issue(registry)
    world = World(created_dad="DUPLICATE")
    _run(tmp_path, registry, world)
    # Exactly one create, for exactly the reserved address. No cycling.
    assert world.creates == 1
    assert {entry["address"] for entry in world.deletes} == {CANDIDATE}


def test_a_duplicate_still_removes_only_the_row_we_created(tmp_path, registry):
    _issue(registry)
    world = World(created_dad="DUPLICATE")
    _run(tmp_path, registry, world)
    assert [entry["address"] for entry in world.deletes] == [CANDIDATE]
    assert [row.address for row in world.rows] == [PRIMARY]


def test_authority_does_not_shorten_the_verified_sequence(tmp_path, registry):
    _issue(registry)
    world = World()
    result = _run(tmp_path, registry, world)
    names = [name for _status, name, _detail in result.steps]
    # Authority is added in front of the Gate 2 sequence, not in place of it.
    assert names.index("reservation-authority") < names.index("create")
    for required in ("dad", "on-link-prefix", "coexistence", "delete",
                     "rollback-verify", "baseline-restored"):
        assert required in names, required


def test_a_successful_run_restores_the_baseline(tmp_path, registry):
    _issue(registry)
    world = World()
    result = _run(tmp_path, registry, world)
    assert result.outcome == "SUCCESS"
    assert result.restored is True
    assert [row.address for row in world.rows] == [PRIMARY]


# --- composition: the three identities stay separate -----------------------

def test_reservation_authority_is_evaluated_by_the_product_not_the_lab(registry):
    # The lab stores a record; the product decides what it is worth. A lab
    # record with a production scope must not become production authority.
    _, reservation, _ = _issue(registry)
    found, assessment = evaluate_gate3_authority(
        candidate_address=CANDIDATE,
        target_prefix=PREFIX,
        environment_id=ENVIRONMENT,
        run_id=RUN,
        registry=registry,
        now=NOW,
    )
    assert found is not None
    assert assessment.usable is True

    from backend.app.recovery_execution import (
        RecoveryAddressReservation,
        assess_recovery_reservation,
    )

    as_product = RecoveryAddressReservation.model_validate(
        to_product_reservation(reservation)
    )
    production = assess_recovery_reservation(
        as_product,
        candidate_address=CANDIDATE,
        management_prefix=PREFIX,
        target_address="",
        gateway_address=None,
        local_addresses=[],
        now=NOW,
        expected_scope="PRODUCTION_NETWORK",
    )
    assert production.usable is False
    assert "RESERVATION_AUTHORITY_UNSUPPORTED" in production.blockers


def test_gate_one_and_two_validation_grants_no_gate_three_authority(tmp_path, registry):
    from backend.app.recovery_capability import (
        current_capability_state,
        dhcp_coexistence_validated,
    )

    assert dhcp_coexistence_validated() is True
    state = current_capability_state()
    assert state.primitive_validated is True
    # Both earlier gates are green, and a Gate 3 run with no reservation still
    # creates nothing.
    result = _run(tmp_path, registry, World())
    assert result.outcome == "AUTHORITY_ABSENT"


def test_gate_three_is_recorded_as_measured_on_a_disposable_adapter():
    from backend.app.recovery_capability import current_capability_state

    entry = next(
        item
        for item in current_capability_state().capabilities
        if item.capability == "COLLISION_SAFE_ADDRESS_AUTHORITY"
    )
    assert entry.status == "VALIDATED"
    # Where it was measured is part of the evidence, not a footnote.
    assert entry.environment == "DISPOSABLE_DHCP_ADAPTER"
    assert entry.observed_at is not None


# --- privacy ---------------------------------------------------------------

def test_the_fixtures_use_documentation_and_benchmark_space_only():
    import ipaddress
    import re

    for address in (PRIMARY, CANDIDATE):
        parsed = ipaddress.ip_address(address)
        assert (
            parsed in ipaddress.ip_network("192.0.2.0/24")
            or parsed in ipaddress.ip_network("198.18.0.0/15")
        ), address
    for value in (ALIAS, ENVIRONMENT, RUN):
        assert re.search(r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}", value) is None
        assert "recovery-env-" not in value
