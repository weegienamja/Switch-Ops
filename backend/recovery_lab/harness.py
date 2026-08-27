"""The experiment: capture, create, wait for DAD, verify, delete, verify again.

Every step is observable and every failure path ends in an attempted rollback
followed by a re-read. The harness never reports success from the fact that a
call returned NO_ERROR -- it reports success from re-reading the address table
and finding what it expected.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Callable, Literal, Sequence

from . import windows_unicast as win
from .journal import (
    OwnedAddress,
    RecoveryJournal,
    fingerprint_addresses,
    new_operation_id,
    now_iso,
)
from .safety import EligibilityDecision, InterfaceFacts, assess_target

StepStatus = Literal["PASS", "FAIL", "SKIPPED"]

Outcome = Literal[
    "SUCCESS",
    "NOT_ELIGIBLE",
    "ADDRESS_CREATE_FAILURE",
    "DAD_DUPLICATE",
    "DAD_TIMEOUT",
    "ROUTE_NOT_ESTABLISHED",
    "COLLATERAL_CHANGE_DETECTED",
    "ADDRESS_DELETE_FAILURE",
    "ROLLBACK_INCOMPLETE",
]

#: IPv4 DAD is documented at roughly three seconds. Allowing well beyond that
#: keeps a slow adapter from being reported as a duplicate, which would be the
#: more dangerous misreading.
DAD_TIMEOUT_SECONDS = 15.0
DAD_POLL_SECONDS = 0.5


@dataclass
class Step:
    name: str
    status: StepStatus
    detail: str = ""


@dataclass
class ExperimentResult:
    outcome: Outcome
    steps: list[Step] = field(default_factory=list)
    operation_id: str = ""
    eligibility: EligibilityDecision | None = None
    #: True only when the interface is left exactly as it was found.
    restored: bool = False
    dad_state: str = "ABSENT"
    elapsed_dad_seconds: float = 0.0

    def add(self, name: str, status: StepStatus, detail: str = "") -> None:
        self.steps.append(Step(name=name, status=status, detail=detail))


@dataclass(frozen=True)
class Baseline:
    """What the world looked like before we touched it."""

    addresses: tuple[tuple[str, int], ...]
    default_route_interfaces: tuple[int, ...]
    dns_servers: tuple[str, ...]
    fingerprint: str


def capture_baseline(
    *,
    addresses: Sequence[tuple[str, int]],
    default_route_interfaces: Sequence[int],
    dns_servers: Sequence[str],
) -> Baseline:
    return Baseline(
        addresses=tuple(sorted(addresses)),
        default_route_interfaces=tuple(sorted(default_route_interfaces)),
        dns_servers=tuple(dns_servers),
        fingerprint=fingerprint_addresses(list(addresses)),
    )


def compare_baseline(before: Baseline, after: Baseline) -> list[str]:
    """Report every way the world differs, ignoring nothing."""
    differences: list[str] = []
    if before.default_route_interfaces != after.default_route_interfaces:
        differences.append("DEFAULT_ROUTE_CHANGED")
    if before.dns_servers != after.dns_servers:
        differences.append("DNS_CHANGED")
    removed = set(before.addresses) - set(after.addresses)
    if removed:
        differences.append("PREEXISTING_ADDRESS_REMOVED")
    return differences


def _default_sleep(seconds: float) -> None:
    time.sleep(seconds)


def run_temporary_address_experiment(
    *,
    interface: InterfaceFacts | None,
    address: str,
    prefix_length: int,
    allowed_interfaces: Sequence[str],
    journal: RecoveryJournal,
    plan_id: str = "harness",
    platform_supported: bool | None = None,
    elevated: bool | None = None,
    read_table: Callable[[], list[win.UnicastAddress]] | None = None,
    create: Callable[..., int] | None = None,
    delete: Callable[..., int] | None = None,
    snapshot: Callable[[], Baseline] | None = None,
    sleep: Callable[[float], None] | None = None,
    dad_timeout: float = DAD_TIMEOUT_SECONDS,
    dhcp_test_authority: bool = False,
    dhcp_coexistence_requested: bool = False,
) -> ExperimentResult:
    """Run one full create/verify/rollback cycle against an isolated adapter.

    The Windows entry points are injectable so the failure paths can be tested
    deterministically without needing a duplicate address on a real network.
    """
    read_table = read_table or win.read_unicast_table
    create = create or win.create_temporary_address
    delete = delete or win.delete_temporary_address
    sleep = sleep or _default_sleep
    platform_supported = (
        win.is_supported() if platform_supported is None else platform_supported
    )
    elevated = win.is_elevated() if elevated is None else elevated

    result = ExperimentResult(outcome="NOT_ELIGIBLE")

    existing = [row.address for row in read_table()] if platform_supported else []
    decision = assess_target(
        interface=interface,
        address=address,
        prefix_length=prefix_length,
        allowed_interfaces=allowed_interfaces,
        platform_supported=platform_supported,
        elevated=elevated,
        existing_addresses=existing,
        dhcp_test_authority=dhcp_test_authority,
        dhcp_coexistence_requested=dhcp_coexistence_requested,
    )
    result.eligibility = decision
    if not decision.eligible:
        result.add("eligibility", "FAIL", ", ".join(decision.blockers))
        result.restored = True  # nothing was attempted
        return result
    result.add("eligibility", "PASS")

    assert interface is not None  # eligibility guarantees this
    before = snapshot() if snapshot else capture_baseline(
        addresses=[(row.address, row.prefix_length) for row in read_table()],
        default_route_interfaces=[],
        dns_servers=[],
    )
    result.add("baseline", "PASS", before.fingerprint)

    operation_id = new_operation_id()
    result.operation_id = operation_id
    owned = OwnedAddress(
        operation_id=operation_id,
        plan_id=plan_id,
        interface_alias=interface.alias,
        interface_index=interface.interface_index,
        interface_luid=interface.interface_luid,
        address=address,
        prefix_length=prefix_length,
        created_at=now_iso(),
        state="INTENT_RECORDED",
        previous_state_fingerprint=before.fingerprint,
    )
    # Recorded before creation: a crash between here and the create leaves a
    # claim with no address, which is safe. The reverse would not be.
    journal.record_intent(owned)
    result.add("journal-intent", "PASS", operation_id)

    rc = create(
        address=address,
        prefix_length=prefix_length,
        interface_index=interface.interface_index,
        interface_luid=interface.interface_luid,
    )
    if rc != win.NO_ERROR:
        journal.update_state(operation_id, "COMPLETED", win.describe_error(rc))
        journal.clear(operation_id)
        result.add("create", "FAIL", win.describe_error(rc))
        result.outcome = "ADDRESS_CREATE_FAILURE"
        result.restored = True
        return result
    journal.update_state(operation_id, "ADDRESS_CREATED")
    result.add("create", "PASS")

    # --- DAD ---------------------------------------------------------------
    waited = 0.0
    observed = None
    while waited <= dad_timeout:
        observed = next(
            (
                row
                for row in read_table()
                if row.address == address
                and row.interface_index == interface.interface_index
                and row.interface_luid == interface.interface_luid
            ),
            None,
        )
        state = observed.dad_state if observed else "ABSENT"
        if state not in ("TENTATIVE", "ABSENT"):
            break
        sleep(DAD_POLL_SECONDS)
        waited += DAD_POLL_SECONDS

    result.elapsed_dad_seconds = waited
    result.dad_state = observed.dad_state if observed else "ABSENT"

    if result.dad_state == "PREFERRED":
        result.add("dad", "PASS", f"preferred after {waited:.1f}s")
    elif result.dad_state == "DUPLICATE":
        result.add("dad", "FAIL", "another host already holds this address")
        return _rollback(result, "DAD_DUPLICATE", journal, owned, delete, read_table)
    else:
        result.add("dad", "FAIL", f"state {result.dad_state} after {waited:.1f}s")
        return _rollback(result, "DAD_TIMEOUT", journal, owned, delete, read_table)

    # --- on-link route -----------------------------------------------------
    if observed is not None and observed.prefix_length != prefix_length:
        # The /32 trap: an address exists but there is no on-link route to the
        # management prefix, so the recovery would not actually reach anything.
        result.add(
            "on-link-prefix",
            "FAIL",
            f"expected /{prefix_length}, observed /{observed.prefix_length}",
        )
        return _rollback(
            result, "ROUTE_NOT_ESTABLISHED", journal, owned, delete, read_table
        )
    result.add("on-link-prefix", "PASS", f"/{prefix_length}")

    # --- collateral --------------------------------------------------------
    after = snapshot() if snapshot else before
    differences = compare_baseline(before, after)
    if differences:
        result.add("collateral", "FAIL", ", ".join(differences))
        return _rollback(
            result, "COLLATERAL_CHANGE_DETECTED", journal, owned, delete, read_table
        )
    result.add("collateral", "PASS", "no unrelated state changed")
    journal.update_state(operation_id, "ADDRESS_VERIFIED")

    return _rollback(result, "SUCCESS", journal, owned, delete, read_table)


def _rollback(
    result: ExperimentResult,
    outcome: Outcome,
    journal: RecoveryJournal,
    owned: OwnedAddress,
    delete: Callable[..., int],
    read_table: Callable[[], list[win.UnicastAddress]],
) -> ExperimentResult:
    """Remove exactly the address we created, then prove it is gone."""
    journal.update_state(owned.operation_id, "ROLLBACK_STARTED")
    rc = delete(
        address=owned.address,
        prefix_length=owned.prefix_length,
        interface_index=owned.interface_index,
        interface_luid=owned.interface_luid,
    )
    if rc != win.NO_ERROR:
        result.add("delete", "FAIL", win.describe_error(rc))
        result.outcome = "ADDRESS_DELETE_FAILURE"
        result.restored = False
        return result
    result.add("delete", "PASS")

    still_present = any(
        row.address == owned.address
        and row.interface_index == owned.interface_index
        and row.interface_luid == owned.interface_luid
        and row.prefix_length == owned.prefix_length
        for row in read_table()
    )
    if still_present:
        result.add("rollback-verify", "FAIL", "address is still present")
        result.outcome = "ROLLBACK_INCOMPLETE"
        result.restored = False
        return result

    result.add("rollback-verify", "PASS", "address removed")
    journal.update_state(owned.operation_id, "COMPLETED")
    journal.clear(owned.operation_id)
    result.outcome = outcome
    result.restored = True
    return result


@dataclass
class RestartFinding:
    disposition: Literal[
        "CLEAN", "RECORDED_ROW_PRESENT", "RECORDED_ROW_ABSENT"
    ]
    records: list[OwnedAddress] = field(default_factory=list)
    detail: str = ""


def assess_restart(
    journal: RecoveryJournal,
    read_table: Callable[[], list[win.UnicastAddress]],
) -> RestartFinding:
    """Describe whether journal-shaped rows are present, without adjudicating.

    This legacy read-only view deliberately does not call a matching description
    ownership. In particular, an intent record has no post-apply evidence and
    can never authorise deletion. Absence also says nothing about why a row went
    away. ``crash-reconcile`` performs the stronger evidence checks.
    """
    outstanding = journal.outstanding()
    if not outstanding:
        return RestartFinding(disposition="CLEAN", detail="No outstanding operations.")

    present = {
        (row.address, row.prefix_length, row.interface_index, row.interface_luid)
        for row in read_table()
    }
    live = [
        record
        for record in outstanding
        if (
            record.address,
            record.prefix_length,
            record.interface_index,
            record.interface_luid,
        )
        in present
    ]
    if live:
        return RestartFinding(
            disposition="RECORDED_ROW_PRESENT",
            records=live,
            detail=(
                f"{len(live)} live row(s) match journal descriptions. This is "
                "not deletion authority; use crash-reconcile for adjudication."
            ),
        )
    return RestartFinding(
        disposition="RECORDED_ROW_ABSENT",
        records=outstanding,
        detail=(
            "No live row matches an outstanding journal description. No cause is "
            "inferred, and this read-only check does not close the journal."
        ),
    )
