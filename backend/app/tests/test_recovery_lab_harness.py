"""The Recovery Lab harness, including the failure paths real hardware won't show.

The Windows entry points are injectable so DAD duplicates, delete failures and
crash-restart can be exercised deterministically. The safety tests matter most:
the harness must refuse the production interface for reasons that survive
elevation, since "we were not admin at the time" is not a safety property.
"""
from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

import pytest

from backend.recovery_lab.harness import (
    assess_restart,
    capture_baseline,
    compare_baseline,
    run_temporary_address_experiment,
)
from backend.recovery_lab.journal import (
    OwnedAddress,
    RecoveryJournal,
    fingerprint_addresses,
    now_iso,
)
from backend.recovery_lab.safety import InterfaceFacts, assess_target, is_documentation_address
from backend.recovery_lab import windows_unicast as win

ISOLATED_LUID = 0x1300000000000000
PRODUCTION_LUID = 0x1600000000000000

ISOLATED = InterfaceFacts(
    interface_index=13,
    interface_luid=ISOLATED_LUID,
    alias="Ethernet 2",
    carries_default_route=False,
    has_dhcp_lease=False,
)
PRODUCTION = InterfaceFacts(
    interface_index=16,
    interface_luid=PRODUCTION_LUID,
    alias="Ethernet",
    carries_default_route=True,
    has_dhcp_lease=True,
)
ADDRESS = "192.0.2.250"


def _row(address: str, *, dad: str = "PREFERRED", prefix: int = 24, index: int = 13,
         luid: int = ISOLATED_LUID):
    return win.UnicastAddress(
        address=address,
        prefix_length=prefix,
        interface_index=index,
        interface_luid=luid,
        prefix_origin="MANUAL",
        suffix_origin="MANUAL",
        dad_state=dad,
        valid_lifetime=0xFFFFFFFF,
        preferred_lifetime=0xFFFFFFFF,
        skip_as_source=False,
    )


def _journal(tmp_path: Path) -> RecoveryJournal:
    return RecoveryJournal(tmp_path / "journal.json")


def _run(tmp_path, *, interface=ISOLATED, table=None, create=None, delete=None,
         elevated=True, dad="PREFERRED", prefix=24, **kwargs):
    """Drive one experiment against an in-memory address table.

    `created` is shared by the default table, create and delete so the fake
    behaves like a real interface: what create adds is what the table reports.
    """
    created: list[str] = []

    def default_table():
        return [_row(address, dad=dad, prefix=prefix) for address in created]

    def default_create(*, address, prefix_length, interface_index, interface_luid):
        created.append(address)
        return win.NO_ERROR

    def default_delete(*, address, prefix_length, interface_index, interface_luid):
        if address in created:
            created.remove(address)
        return win.NO_ERROR

    return run_temporary_address_experiment(
        interface=interface,
        address=ADDRESS,
        prefix_length=24,
        allowed_interfaces=["Ethernet 2"],
        journal=_journal(tmp_path),
        platform_supported=True,
        elevated=elevated,
        read_table=table or default_table,
        create=create or default_create,
        delete=delete or default_delete,
        sleep=lambda _seconds: None,
        **kwargs,
    )


# --- safety ----------------------------------------------------------------

def test_the_production_interface_is_refused_for_reasons_that_survive_elevation():
    decision = assess_target(
        interface=PRODUCTION,
        address=ADDRESS,
        prefix_length=24,
        allowed_interfaces=["Ethernet"],   # even when explicitly named
        platform_supported=True,
        elevated=True,                     # even when elevated
        existing_addresses=[],
    )
    assert decision.eligible is False
    assert "INTERFACE_CARRIES_DEFAULT_ROUTE" in decision.blockers
    assert "INTERFACE_HAS_DHCP_LEASE" in decision.blockers


def test_an_unnamed_interface_is_refused_even_if_otherwise_safe():
    decision = assess_target(
        interface=ISOLATED,
        address=ADDRESS,
        prefix_length=24,
        allowed_interfaces=[],
        platform_supported=True,
        elevated=True,
    )
    assert decision.eligible is False
    assert "INTERFACE_NOT_EXPLICITLY_ALLOWED" in decision.blockers


def test_only_documentation_addresses_may_be_created():
    assert is_documentation_address("192.0.2.250") is True
    assert is_documentation_address("192.168.0.95") is False
    decision = assess_target(
        interface=ISOLATED,
        address="192.168.0.95",
        prefix_length=24,
        allowed_interfaces=["Ethernet 2"],
        platform_supported=True,
        elevated=True,
    )
    assert "ADDRESS_NOT_DOCUMENTATION_RANGE" in decision.blockers


def test_an_address_that_already_exists_is_never_claimed():
    decision = assess_target(
        interface=ISOLATED,
        address=ADDRESS,
        prefix_length=24,
        allowed_interfaces=["Ethernet 2"],
        platform_supported=True,
        elevated=True,
        existing_addresses=[ADDRESS],
    )
    assert "ADDRESS_ALREADY_PRESENT" in decision.blockers


def test_without_elevation_nothing_is_attempted(tmp_path):
    result = _run(tmp_path, elevated=False)
    assert result.outcome == "NOT_ELIGIBLE"
    assert result.restored is True
    assert "ELEVATION_UNAVAILABLE" in result.eligibility.blockers


# --- happy path ------------------------------------------------------------

def test_a_clean_cycle_creates_verifies_and_removes_exactly_one_address(tmp_path):
    result = _run(tmp_path)
    assert result.outcome == "SUCCESS"
    assert result.restored is True
    assert result.dad_state == "PREFERRED"
    names = [step.name for step in result.steps]
    assert names == [
        "eligibility", "baseline", "journal-intent", "create",
        "dad", "on-link-prefix", "collateral", "delete", "rollback-verify",
    ]
    assert all(step.status == "PASS" for step in result.steps)


def test_the_journal_is_empty_once_the_address_is_confirmed_gone(tmp_path):
    journal_path = tmp_path / "journal.json"
    journal = RecoveryJournal(journal_path)
    created: list[str] = []
    run_temporary_address_experiment(
        interface=ISOLATED,
        address=ADDRESS,
        prefix_length=24,
        allowed_interfaces=["Ethernet 2"],
        journal=journal,
        platform_supported=True,
        elevated=True,
        read_table=lambda: [_row(a) for a in created],
        create=lambda **kw: (created.append(kw["address"]), win.NO_ERROR)[1],
        delete=lambda **kw: (created.remove(kw["address"]), win.NO_ERROR)[1],
        sleep=lambda _s: None,
    )
    assert journal.outstanding() == []
    assert json.loads(journal_path.read_text(encoding="utf-8")) == []


# --- failure paths ---------------------------------------------------------

def test_a_create_failure_leaves_no_journal_claim(tmp_path):
    journal = RecoveryJournal(tmp_path / "journal.json")
    result = run_temporary_address_experiment(
        interface=ISOLATED,
        address=ADDRESS,
        prefix_length=24,
        allowed_interfaces=["Ethernet 2"],
        journal=journal,
        platform_supported=True,
        elevated=True,
        read_table=lambda: [],
        create=lambda **kw: win.ERROR_ACCESS_DENIED,
        delete=lambda **kw: win.NO_ERROR,
        sleep=lambda _s: None,
    )
    assert result.outcome == "ADDRESS_CREATE_FAILURE"
    assert result.restored is True
    assert journal.outstanding() == []


def test_a_duplicate_address_is_detected_and_rolled_back(tmp_path):
    # The whole point of waiting for DAD: somebody else already has it.
    result = _run(tmp_path, dad="DUPLICATE")
    assert result.outcome == "DAD_DUPLICATE"
    assert result.restored is True
    assert any(s.name == "delete" and s.status == "PASS" for s in result.steps)


def test_dad_that_never_settles_is_a_timeout_not_a_success(tmp_path):
    result = _run(tmp_path, dad="TENTATIVE", dad_timeout=2.0)
    assert result.outcome == "DAD_TIMEOUT"
    assert result.restored is True


def test_a_slash_32_is_treated_as_a_failed_recovery(tmp_path):
    # OnLinkPrefixLength defaults to 255, which Windows turns into /32. The
    # address would exist with no on-link route to the management prefix.
    result = _run(tmp_path, prefix=32)
    assert result.outcome == "ROUTE_NOT_ESTABLISHED"
    assert result.restored is True


def test_a_delete_failure_is_reported_as_not_restored(tmp_path):
    created: list[str] = []

    def create(**kw):
        created.append(kw["address"])
        return win.NO_ERROR

    result = _run(
        tmp_path,
        table=lambda: [_row(a) for a in created],
        create=create,
        delete=lambda **kw: win.ERROR_ACCESS_DENIED,
    )
    assert result.outcome == "ADDRESS_DELETE_FAILURE"
    assert result.restored is False


def test_a_delete_that_reports_success_but_leaves_the_address_is_incomplete(tmp_path):
    created: list[str] = []

    def create(**kw):
        created.append(kw["address"])
        return win.NO_ERROR

    # Lies about having removed it. The harness must not believe the return code.
    result = _run(
        tmp_path,
        table=lambda: [_row(a) for a in created],
        create=create,
        delete=lambda **kw: win.NO_ERROR,
    )
    assert result.outcome == "ROLLBACK_INCOMPLETE"
    assert result.restored is False


def test_collateral_change_triggers_rollback(tmp_path):
    created: list[str] = []
    before = capture_baseline(
        addresses=[("192.168.56.1", 24)],
        default_route_interfaces=[16],
        dns_servers=["192.0.2.53"],
    )
    after = capture_baseline(
        addresses=[("192.168.56.1", 24)],
        default_route_interfaces=[18],   # default route moved
        dns_servers=["192.0.2.53"],
    )
    snapshots = iter([before, after])

    result = _run(
        tmp_path,
        table=lambda: [_row(a) for a in created],
        create=lambda **kw: (created.append(kw["address"]), win.NO_ERROR)[1],
        delete=lambda **kw: (created.remove(kw["address"]), win.NO_ERROR)[1],
        snapshot=lambda: next(snapshots),
    )
    assert result.outcome == "COLLATERAL_CHANGE_DETECTED"
    assert result.restored is True


def test_compare_baseline_names_each_kind_of_collateral_change():
    before = capture_baseline(
        addresses=[("192.0.2.5", 24)], default_route_interfaces=[16],
        dns_servers=["192.0.2.53"],
    )
    assert compare_baseline(before, before) == []
    moved_route = capture_baseline(
        addresses=[("192.0.2.5", 24)], default_route_interfaces=[18],
        dns_servers=["192.0.2.53"],
    )
    assert compare_baseline(before, moved_route) == ["DEFAULT_ROUTE_CHANGED"]
    changed_dns = capture_baseline(
        addresses=[("192.0.2.5", 24)], default_route_interfaces=[16],
        dns_servers=["198.51.100.53"],
    )
    assert compare_baseline(before, changed_dns) == ["DNS_CHANGED"]
    lost_address = capture_baseline(
        addresses=[], default_route_interfaces=[16], dns_servers=["192.0.2.53"],
    )
    assert compare_baseline(before, lost_address) == ["PREEXISTING_ADDRESS_REMOVED"]


# --- crash and restart -----------------------------------------------------

def _owned(**overrides) -> OwnedAddress:
    payload = {
        "operation_id": "recovery-op-abc123",
        "plan_id": "plan-1",
        "interface_alias": "Ethernet 2",
        "interface_index": 13,
        "interface_luid": ISOLATED_LUID,
        "address": ADDRESS,
        "prefix_length": 24,
        "created_at": now_iso(),
        "state": "INTENT_RECORDED",
    }
    payload.update(overrides)
    return OwnedAddress(**payload)


def test_restart_check_reports_a_matching_description_without_claiming_ownership(
    tmp_path,
):
    journal = _journal(tmp_path)
    journal.record_intent(_owned())
    finding = assess_restart(journal, lambda: [_row(ADDRESS)])
    assert finding.disposition == "RECORDED_ROW_PRESENT"
    assert finding.records[0].address == ADDRESS
    assert "not deletion authority" in finding.detail


def test_restart_check_does_not_infer_why_a_recorded_row_is_absent(tmp_path):
    journal = _journal(tmp_path)
    journal.record_intent(_owned())
    finding = assess_restart(journal, lambda: [])
    assert finding.disposition == "RECORDED_ROW_ABSENT"
    assert "No cause is inferred" in finding.detail
    assert "reboot" not in finding.detail


def test_a_journal_with_nothing_outstanding_is_clean(tmp_path):
    assert assess_restart(_journal(tmp_path), lambda: []).disposition == "CLEAN"


def test_restart_never_claims_an_address_it_did_not_record(tmp_path):
    journal = _journal(tmp_path)
    journal.record_intent(_owned())
    # A different address on the same interface is somebody else's.
    finding = assess_restart(journal, lambda: [_row("192.0.2.99")])
    assert finding.disposition == "RECORDED_ROW_ABSENT"


def test_ownership_matching_is_exact_on_every_identity_field():
    owned = _owned()
    assert owned.matches(
        address=ADDRESS, prefix_length=24, interface_index=13,
        interface_luid=ISOLATED_LUID,
    )
    for wrong in (
        {"address": "192.0.2.99"},
        {"prefix_length": 25},
        {"interface_index": 16},
        {"interface_luid": 999},
    ):
        kwargs = {
            "address": ADDRESS,
            "prefix_length": 24,
            "interface_index": 13,
            "interface_luid": ISOLATED_LUID,
        }
        kwargs.update(wrong)
        assert owned.matches(**kwargs) is False, wrong


def test_a_corrupt_journal_is_not_read_as_nothing_owned(tmp_path):
    path = tmp_path / "journal.json"
    path.write_text("{ this is not json", encoding="utf-8")
    with pytest.raises(RuntimeError):
        RecoveryJournal(path).outstanding()


def test_fingerprints_are_stable_and_order_independent():
    a = fingerprint_addresses([("192.0.2.5", 24), ("192.0.2.6", 24)])
    b = fingerprint_addresses([("192.0.2.6", 24), ("192.0.2.5", 24)])
    assert a == b
    assert a != fingerprint_addresses([("192.0.2.7", 24)])


# --- production isolation --------------------------------------------------

def test_no_production_module_imports_the_recovery_lab():
    # This package is the only code in the repository that can change host
    # addressing. If the product ever imported it, the planning-only boundary
    # would be one code path away from gone.
    app_root = Path(__file__).resolve().parents[1]
    offenders = [
        path.name
        for path in app_root.rglob("*.py")
        if "tests" not in path.parts
        and "__pycache__" not in path.parts
        and "recovery_lab" in path.read_text(encoding="utf-8")
    ]
    assert offenders == []


def test_the_packaged_sidecar_does_not_bundle_the_recovery_lab():
    repo_root = Path(__file__).resolve().parents[3]
    spec = (repo_root / "backend" / "sidecar.py").read_text(encoding="utf-8")
    assert "recovery_lab" not in spec


def test_mutating_entry_points_live_only_in_the_recovery_lab():
    repo_root = Path(__file__).resolve().parents[3]
    app_root = repo_root / "backend" / "app"
    for name in ("CreateUnicastIpAddressEntry", "DeleteUnicastIpAddressEntry"):
        offenders = [
            path.name
            for path in app_root.rglob("*.py")
            if "__pycache__" not in path.parts
            and "tests" not in path.parts
            and name in path.read_text(encoding="utf-8")
        ]
        assert offenders == [], (name, offenders)


def test_the_product_still_declares_itself_planning_only():
    from backend.app.recovery_execution import RecoveryExecutionArchitecture

    fields = RecoveryExecutionArchitecture.model_fields
    assert fields["mode"].default == "PLANNING_ONLY"
    assert fields["executor_implemented"].default is False
    assert fields["approval_available"].default is False
