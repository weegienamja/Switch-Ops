"""The Gate 2 experiment, end to end.

The point of this evaluator is that a SUCCESS means what the capability model
says it means. The generic temporary-address experiment never reads the DHCP
primary, the default routes, or DNS, so it could report SUCCESS while having
proven none of them.

The most important assertions here are the ones counting ``creates_attempted``:
if the baseline cannot establish that the interface really is DHCP-controlled,
nothing may be created at all.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from backend.recovery_lab.coexistence import (
    NetworkSnapshot,
    capture_dhcp_baseline,
    run_dhcp_coexistence_experiment,
)
from backend.recovery_lab.journal import RecoveryJournal
from backend.recovery_lab.windows_unicast import (
    ERROR_ACCESS_DENIED,
    NO_ERROR,
    UnicastAddress,
)

IFINDEX = 58
LUID = 0x3A00000000000000
ALIAS = "Ethernet 3"
PRIMARY = "192.168.57.101"          # DHCP-served by the disposable environment
TEMPORARY = "192.0.2.250"           # RFC 5737, the simulated management prefix
PREFIX = "192.0.2.0/24"


def _row(address, *, origin=("DHCP", "DHCP"), dad="PREFERRED", lifetime=3600,
         prefix=24, index=IFINDEX, luid=LUID, ts=0):
    return UnicastAddress(
        address=address, prefix_length=prefix, interface_index=index,
        interface_luid=luid, prefix_origin=origin[0], suffix_origin=origin[1],
        dad_state=dad, valid_lifetime=lifetime, preferred_lifetime=lifetime,
        skip_as_source=False, creation_timestamp=ts,
    )


def _manual(address=TEMPORARY, **kw):
    kw.setdefault("origin", ("MANUAL", "MANUAL"))
    kw.setdefault("lifetime", 0xFFFFFFFF)
    return _row(address, **kw)


def _snapshot(routes=("192.168.57.0/24",), dns=("203.0.113.53",),
              defaults=((16, "203.0.113.1"),), addresses=((PRIMARY, 24),)):
    return NetworkSnapshot(
        interface_addresses=tuple(addresses),
        interface_routes=tuple(routes),
        default_routes=tuple(defaults),
        dns_servers=tuple(dns),
    )


class World:
    """A fake interface whose address table responds to create and delete."""

    def __init__(self, *, rows=None, create_code=NO_ERROR, delete_code=NO_ERROR,
                 created_dad="PREFERRED", created_prefix=24,
                 delete_actually_removes=True, after_snapshot=None,
                 mutate_on_create=None, created_ts=0):
        self.rows = list(rows if rows is not None else [_row(PRIMARY)])
        self.create_code = create_code
        self.delete_code = delete_code
        self.created_dad = created_dad
        self.created_prefix = created_prefix
        self.delete_actually_removes = delete_actually_removes
        self.after_snapshot = after_snapshot
        self.mutate_on_create = mutate_on_create
        self.created_ts = created_ts
        self.creates = 0
        self.deletes: list[dict] = []
        self._created = False

    def read_table(self):
        return list(self.rows)

    def read_snapshot(self):
        if self._created and self.after_snapshot is not None:
            return self.after_snapshot
        routes = ["192.168.57.0/24"] + (["192.0.2.0/24"] if self._created else [])
        addresses = [(row.address, row.prefix_length) for row in self.rows]
        return _snapshot(routes=tuple(routes), addresses=tuple(addresses))

    def create(self, *, address, prefix_length, interface_index, interface_luid):
        self.creates += 1
        if self.create_code != NO_ERROR:
            return self.create_code
        self.rows.append(
            _manual(
                address,
                dad=self.created_dad,
                prefix=self.created_prefix,
                ts=self.created_ts,
            )
        )
        self._created = True
        if self.mutate_on_create:
            self.mutate_on_create(self)
        return NO_ERROR

    def delete(self, *, address, prefix_length, interface_index, interface_luid):
        self.deletes.append(
            {
                "address": address, "prefix_length": prefix_length,
                "interface_index": interface_index, "interface_luid": interface_luid,
            }
        )
        if self.delete_code != NO_ERROR:
            return self.delete_code
        if self.delete_actually_removes:
            self.rows = [row for row in self.rows if row.address != address]
            self._created = False
        return NO_ERROR


def _run(tmp_path, world, *, authority=True, journal=None):
    return run_dhcp_coexistence_experiment(
        interface_index=IFINDEX,
        interface_luid=LUID,
        interface_alias=ALIAS,
        temporary_address=TEMPORARY,
        prefix_length=24,
        expected_on_link_prefix=PREFIX,
        authority_granted=authority,
        journal=journal or RecoveryJournal(tmp_path / "journal.json"),
        read_table=world.read_table,
        read_snapshot=world.read_snapshot,
        create=world.create,
        delete=world.delete,
        sleep=lambda _s: None,
        dad_timeout=2.0,
    )


# --- fail before mutation --------------------------------------------------

def test_without_authority_nothing_is_created(tmp_path):
    world = World()
    result = _run(tmp_path, world, authority=False)
    assert result.outcome == "NOT_AUTHORISED"
    assert world.creates == 0
    assert result.restored is True


def test_a_missing_dhcp_primary_creates_nothing(tmp_path):
    world = World(rows=[])
    result = _run(tmp_path, world)
    assert result.outcome == "BASELINE_INCOMPLETE"
    assert result.baseline_outcome == "PRIMARY_ABSENT"
    assert world.creates == 0


def test_a_non_dhcp_primary_creates_nothing(tmp_path):
    # A statically addressed adapter cannot answer the coexistence question.
    world = World(rows=[_row(PRIMARY, origin=("MANUAL", "MANUAL"))])
    result = _run(tmp_path, world)
    assert result.outcome == "BASELINE_INCOMPLETE"
    assert result.baseline_outcome == "PRIMARY_NOT_DHCP"
    assert world.creates == 0


def test_a_tentative_dhcp_primary_creates_nothing(tmp_path):
    world = World(rows=[_row(PRIMARY, dad="TENTATIVE")])
    result = _run(tmp_path, world)
    assert result.baseline_outcome == "PRIMARY_NOT_PREFERRED"
    assert world.creates == 0


def test_an_infinite_lease_creates_nothing(tmp_path):
    # Infinite lifetime means it is not behaving as a lease.
    world = World(rows=[_row(PRIMARY, lifetime=0xFFFFFFFF)])
    result = _run(tmp_path, world)
    assert result.baseline_outcome == "PRIMARY_LEASE_NOT_FINITE"
    assert world.creates == 0


def test_an_already_present_temporary_address_creates_nothing(tmp_path):
    world = World(rows=[_row(PRIMARY), _manual(TEMPORARY)])
    result = _run(tmp_path, world)
    assert result.baseline_outcome == "TEMPORARY_ADDRESS_ALREADY_PRESENT"
    assert world.creates == 0


def test_two_dhcp_primaries_are_ambiguous_and_create_nothing(tmp_path):
    world = World(rows=[_row(PRIMARY), _row("192.168.57.150")])
    result = _run(tmp_path, world)
    assert result.baseline_outcome == "PRIMARY_AMBIGUOUS"
    assert world.creates == 0


def test_an_unresolved_luid_creates_nothing(tmp_path):
    world = World()
    result = run_dhcp_coexistence_experiment(
        interface_index=IFINDEX, interface_luid=0, interface_alias=ALIAS,
        temporary_address=TEMPORARY, prefix_length=24,
        expected_on_link_prefix=PREFIX, authority_granted=True,
        journal=RecoveryJournal(tmp_path / "journal.json"),
        read_table=world.read_table, read_snapshot=world.read_snapshot,
        create=world.create, delete=world.delete, sleep=lambda _s: None,
    )
    assert result.baseline_outcome == "IDENTITY_NOT_RESOLVED"
    assert world.creates == 0


# --- the success shape -----------------------------------------------------

def test_a_clean_run_measures_and_restores(tmp_path):
    world = World()
    result = _run(tmp_path, world)
    assert result.outcome == "SUCCESS", (result.findings, result.evidence)
    assert result.restored is True
    assert result.dad_state == "PREFERRED"
    assert world.creates == 1
    assert len(world.deletes) == 1

    names = [name for _status, name, _detail in result.steps]
    assert names == [
        "authority", "dhcp-baseline", "journal-intent", "create",
        # Post-apply ownership evidence is written before DAD is even polled,
        # so a crash from here on is reconcilable by a new process.
        "journal-created", "dad",
        "on-link-prefix", "coexistence", "delete", "rollback-verify",
        "baseline-restored",
    ]
    joined = " ".join(result.evidence)
    assert "still DHCP/DHCP" in joined
    assert "Default routes are unchanged" in joined


def test_the_journal_is_cleared_only_after_confirmed_removal(tmp_path):
    journal_path = tmp_path / "journal.json"
    world = World()
    run_dhcp_coexistence_experiment(
        interface_index=IFINDEX, interface_luid=LUID, interface_alias=ALIAS,
        temporary_address=TEMPORARY, prefix_length=24,
        expected_on_link_prefix=PREFIX, authority_granted=True,
        journal=RecoveryJournal(journal_path),
        read_table=world.read_table, read_snapshot=world.read_snapshot,
        create=world.create, delete=world.delete, sleep=lambda _s: None,
    )
    assert RecoveryJournal(journal_path).outstanding() == []


def test_deletion_targets_the_exact_owned_row(tmp_path):
    world = World()
    _run(tmp_path, world)
    assert world.deletes == [
        {
            "address": TEMPORARY, "prefix_length": 24,
            "interface_index": IFINDEX, "interface_luid": LUID,
        }
    ]


# --- post-create failures all roll back ------------------------------------

def test_a_create_failure_is_reported_and_nothing_lingers(tmp_path):
    world = World(create_code=ERROR_ACCESS_DENIED)
    result = _run(tmp_path, world)
    assert result.outcome == "ADDRESS_CREATE_FAILURE"
    assert result.restored is True
    assert world.deletes == []


def test_post_apply_journal_failure_still_rolls_back_the_created_row(tmp_path):
    class FailedCreatedJournal(RecoveryJournal):
        def record_created(self, *args, **kwargs):
            raise OSError("synthetic post-apply persistence failure")

    journal = FailedCreatedJournal(tmp_path / "journal.json")
    world = World(created_ts=134322627952300878)
    result = _run(tmp_path, world, journal=journal)
    assert result.outcome == "JOURNAL_PERSISTENCE_FAILURE"
    assert result.restored is True
    assert len(world.deletes) == 1
    assert [row.address for row in world.rows] == [PRIMARY]
    assert journal.outstanding() == []


def test_a_duplicate_address_rolls_back(tmp_path):
    world = World(created_dad="DUPLICATE")
    result = _run(tmp_path, world)
    assert result.outcome == "DAD_DUPLICATE"
    assert result.restored is True
    assert len(world.deletes) == 1


def test_dad_that_never_settles_rolls_back(tmp_path):
    world = World(created_dad="TENTATIVE")
    result = _run(tmp_path, world)
    assert result.outcome == "DAD_TIMEOUT"
    assert result.restored is True
    assert len(world.deletes) == 1


def test_a_slash_32_rolls_back(tmp_path):
    # The initialised OnLinkPrefixLength trap: an address with no on-link route.
    world = World(created_prefix=32)
    result = _run(tmp_path, world)
    assert result.outcome == "ROUTE_NOT_ESTABLISHED"
    assert result.restored is True
    assert len(world.deletes) == 1


def _breaks(mutation):
    def apply(world):
        mutation(world)
    return apply


def test_a_primary_that_stops_being_dhcp_fails_and_rolls_back(tmp_path):
    def flip(world):
        world.rows = [
            _row(PRIMARY, origin=("MANUAL", "MANUAL")) if row.address == PRIMARY else row
            for row in world.rows
        ]

    world = World(mutate_on_create=_breaks(flip))
    result = _run(tmp_path, world)
    assert "PRIMARY_NO_LONGER_DHCP" in result.findings
    # The primary does not repair itself, so cleanup cannot restore the
    # baseline either. Reporting restored=True here would be a lie.
    assert result.outcome == "BASELINE_NOT_RESTORED"
    assert result.restored is False
    assert len(world.deletes) == 1


def test_a_disappearing_primary_fails_and_rolls_back(tmp_path):
    def drop(world):
        world.rows = [row for row in world.rows if row.address != PRIMARY]

    world = World(mutate_on_create=_breaks(drop))
    result = _run(tmp_path, world)
    assert "PRIMARY_ADDRESS_MISSING" in result.findings
    assert result.outcome == "BASELINE_NOT_RESTORED"
    assert result.restored is False
    assert len(world.deletes) == 1


def test_a_changed_default_route_fails_and_rolls_back(tmp_path):
    world = World(
        after_snapshot=_snapshot(
            routes=("192.168.57.0/24", "192.0.2.0/24"),
            defaults=((18, "203.0.113.1"),),
        )
    )
    result = _run(tmp_path, world)
    assert result.outcome == "COEXISTENCE_VIOLATED"
    assert "DEFAULT_ROUTE_CHANGED" in result.findings
    assert result.restored is True


def test_changed_dns_fails_and_rolls_back(tmp_path):
    world = World(
        after_snapshot=_snapshot(
            routes=("192.168.57.0/24", "192.0.2.0/24"), dns=("198.51.100.53",)
        )
    )
    result = _run(tmp_path, world)
    assert result.outcome == "COEXISTENCE_VIOLATED"
    assert "DNS_CHANGED" in result.findings


def test_a_missing_on_link_route_fails_and_rolls_back(tmp_path):
    world = World(after_snapshot=_snapshot(routes=("192.168.57.0/24",)))
    result = _run(tmp_path, world)
    assert result.outcome == "COEXISTENCE_VIOLATED"
    assert "ON_LINK_ROUTE_MISSING" in result.findings


def test_a_lost_unrelated_address_fails_and_rolls_back(tmp_path):
    world = World(
        after_snapshot=_snapshot(
            routes=("192.168.57.0/24", "192.0.2.0/24"), addresses=(),
        )
    )
    result = _run(tmp_path, world)
    assert result.outcome == "COEXISTENCE_VIOLATED"
    assert "UNRELATED_ADDRESS_REMOVED" in result.findings
    assert result.restored is True


# --- cleanup honesty -------------------------------------------------------

def test_a_delete_failure_reports_not_restored(tmp_path):
    world = World(delete_code=ERROR_ACCESS_DENIED)
    result = _run(tmp_path, world)
    assert result.outcome == "ADDRESS_DELETE_FAILURE"
    assert result.restored is False


def test_a_delete_that_lies_reports_rollback_incomplete(tmp_path):
    # The return code is never the answer; the table is re-read.
    world = World(delete_actually_removes=False)
    result = _run(tmp_path, world)
    assert result.outcome == "ROLLBACK_INCOMPLETE"
    assert result.restored is False


def test_a_primary_lost_during_cleanup_is_not_reported_as_restored(tmp_path):
    world = World()

    def delete(*, address, prefix_length, interface_index, interface_luid):
        world.deletes.append({"address": address})
        world.rows = []          # temporary gone, but so is the primary
        return NO_ERROR

    world.delete = delete
    result = _run(tmp_path, world)
    assert result.outcome == "BASELINE_NOT_RESTORED"
    assert result.restored is False


# --- baseline capture in isolation -----------------------------------------

def test_a_captured_baseline_records_the_primary_and_surroundings():
    capture = capture_dhcp_baseline(
        interface_index=IFINDEX, interface_luid=LUID,
        temporary_address=TEMPORARY, rows=[_row(PRIMARY)], snapshot=_snapshot(),
    )
    assert capture.outcome == "CAPTURED"
    assert capture.baseline is not None
    assert capture.baseline.primary.address == PRIMARY
    assert "lease" in " ".join(capture.evidence)


def test_rows_on_other_interfaces_are_ignored_by_the_baseline():
    capture = capture_dhcp_baseline(
        interface_index=IFINDEX, interface_luid=LUID,
        temporary_address=TEMPORARY,
        rows=[_row("192.168.254.5", index=16, luid=0x1000000000000000)],
        snapshot=_snapshot(),
    )
    assert capture.outcome == "PRIMARY_ABSENT"


# --- the CLI actually dispatches this evaluator ----------------------------
#
# The defect this replaces was that `--dhcp-coexistence` granted authority and
# then ran the *generic* temporary-address experiment, which never reads the
# DHCP primary. A SUCCESS from that command would not have meant what the
# capability model says DHCP coexistence means.

def test_the_dhcp_coexistence_flag_runs_the_dedicated_evaluator(monkeypatch, capsys):
    import argparse

    from backend.recovery_lab import __main__ as cli
    from backend.recovery_lab.safety import InterfaceFacts

    called = {}

    def fake_runner(**kwargs):
        called.update(kwargs)
        from backend.recovery_lab.coexistence import CoexistenceRunResult

        return CoexistenceRunResult(outcome="SUCCESS", restored=True)

    def unexpected(**kwargs):  # pragma: no cover - must not be reached
        raise AssertionError("the generic experiment must not run for Gate 2")

    interface = InterfaceFacts(
        interface_index=58, interface_luid=LUID, alias=ALIAS,
        carries_default_route=False, has_dhcp_lease=True,
    )
    monkeypatch.setattr(cli, "gather_interfaces", lambda: {ALIAS: interface})
    monkeypatch.setattr(cli, "gather_windows_adapters", lambda: [])
    monkeypatch.setattr(cli, "gather_hostonly_guids", lambda: {})
    monkeypatch.setattr(cli, "gather_network_snapshot", lambda _index: _snapshot())
    monkeypatch.setattr(cli, "run_dhcp_coexistence_experiment", fake_runner)
    monkeypatch.setattr(cli, "run_temporary_address_experiment", unexpected)
    monkeypatch.setattr(cli.win, "is_supported", lambda: True)
    monkeypatch.setattr(cli.win, "is_elevated", lambda: True)
    monkeypatch.setattr(cli.win, "read_unicast_table", lambda: [])

    # Authority is stubbed as granted so the dispatch itself is what is tested;
    # the authority chain has its own tests.
    from backend.recovery_lab import environment as env

    monkeypatch.setattr(
        env, "assess_test_authority",
        lambda **kw: env.ExperimentAuthority(
            granted=True, provenance="DISPOSABLE_DHCP_ENVIRONMENT"
        ),
    )

    exit_code = cli.command_experiment(
        argparse.Namespace(
            interface=ALIAS, address=TEMPORARY, prefix_length=24, allow=[ALIAS],
            journal="ignored", registry="ignored", dhcp_coexistence=True,
        )
    )

    assert exit_code == 0
    assert called, "the dedicated coexistence evaluator was never called"
    assert called["temporary_address"] == TEMPORARY
    assert called["interface_luid"] == LUID
    assert called["expected_on_link_prefix"] == "192.0.2.0/24"
    assert called["authority_granted"] is True
    assert "DHCP_COEXISTENCE" in capsys.readouterr().out
