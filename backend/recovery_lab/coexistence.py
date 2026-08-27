"""Did the temporary address disturb the DHCP-controlled primary?

Success is not "the same IP is still there". A primary address can survive as a
string while having quietly stopped being DHCP-controlled, lost its lease, or
had its origin rewritten to manual -- which is exactly the failure mode that
makes `New-NetIPAddress` unsafe. So preservation is asserted on properties.

Lease lifetime naturally decreases with time, so it is checked for *presence and
plausibility*, never for equality.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal, Sequence

from .windows_unicast import UnicastAddress

CoexistenceFinding = Literal[
    "PRIMARY_ADDRESS_MISSING",
    "PRIMARY_NO_LONGER_DHCP",
    "PRIMARY_NOT_PREFERRED",
    "PRIMARY_LEASE_LOST",
    "PRIMARY_LEASE_EXTENDED_IMPLAUSIBLY",
    "PRIMARY_PREFIX_CHANGED",
    "TEMPORARY_ADDRESS_MISSING",
    "TEMPORARY_ADDRESS_NOT_PREFERRED",
    "TEMPORARY_PREFIX_WRONG",
    "TEMPORARY_ADDRESS_IS_DHCP",
    "DEFAULT_ROUTE_CHANGED",
    "DNS_CHANGED",
    "ON_LINK_ROUTE_MISSING",
    "UNRELATED_ROUTE_CHANGED",
    "UNRELATED_ADDRESS_REMOVED",
    "SOURCE_SELECTION_CHANGED",
]


@dataclass(frozen=True)
class NetworkSnapshot:
    """Everything the coexistence check compares, captured at one instant."""

    #: (address, prefix_length) for the interface under test.
    interface_addresses: tuple[tuple[str, int], ...]
    #: Route destination prefixes present on the interface under test.
    interface_routes: tuple[str, ...]
    #: (interface_index, next_hop) for every default route on the machine.
    default_routes: tuple[tuple[int, str], ...]
    dns_servers: tuple[str, ...]
    #: Which source address Windows picks for a given destination, where the
    #: harness was able to measure it. Empty when not measured.
    source_selection: tuple[tuple[str, str], ...] = ()


@dataclass
class CoexistenceResult:
    preserved: bool
    findings: list[CoexistenceFinding] = field(default_factory=list)
    evidence: list[str] = field(default_factory=list)

    def note(self, message: str) -> None:
        self.evidence.append(message)

    def fail(self, finding: CoexistenceFinding, message: str) -> None:
        self.findings.append(finding)
        self.evidence.append(message)


def _find(rows: Sequence[UnicastAddress], address: str) -> UnicastAddress | None:
    return next((row for row in rows if row.address == address), None)


def evaluate_dhcp_coexistence(
    *,
    primary_address: str,
    primary_prefix_length: int,
    temporary_address: str,
    temporary_prefix_length: int,
    baseline_primary: UnicastAddress,
    observed: Sequence[UnicastAddress],
    before: NetworkSnapshot,
    after: NetworkSnapshot,
    expected_on_link_prefix: str,
    elapsed_seconds: float,
) -> CoexistenceResult:
    """Compare the whole interface before and after adding a temporary address."""
    result = CoexistenceResult(preserved=True)

    # --- the DHCP-controlled primary ---------------------------------------
    primary = _find(observed, primary_address)
    if primary is None:
        result.fail(
            "PRIMARY_ADDRESS_MISSING",
            f"The DHCP primary {primary_address} is no longer present.",
        )
    else:
        if not primary.is_dhcp:
            # The failure mode that makes EnableStatic unsafe: the address
            # survives but stops being DHCP-controlled.
            result.fail(
                "PRIMARY_NO_LONGER_DHCP",
                f"{primary_address} is now {primary.prefix_origin}/"
                f"{primary.suffix_origin} rather than DHCP/DHCP.",
            )
        else:
            result.note(f"{primary_address} is still DHCP/DHCP.")

        if not primary.is_usable:
            result.fail(
                "PRIMARY_NOT_PREFERRED",
                f"{primary_address} is {primary.dad_state}, not preferred.",
            )
        if primary.prefix_length != primary_prefix_length:
            result.fail(
                "PRIMARY_PREFIX_CHANGED",
                f"{primary_address} changed from /{primary_prefix_length} to "
                f"/{primary.prefix_length}.",
            )

        if not primary.has_finite_lease:
            # An infinite lifetime means it is no longer a lease.
            result.fail(
                "PRIMARY_LEASE_LOST",
                f"{primary_address} now has an infinite lifetime, so it is no "
                "longer a DHCP lease.",
            )
        else:
            # A lease counts down. It may be renewed (jumping up), but it should
            # not exceed the original by more than the time that has passed
            # unless something re-leased it, which is worth noticing.
            drift = primary.valid_lifetime - baseline_primary.valid_lifetime
            if drift > max(elapsed_seconds * 2, 60):
                result.fail(
                    "PRIMARY_LEASE_EXTENDED_IMPLAUSIBLY",
                    f"The lease on {primary_address} grew by {drift}s in "
                    f"{elapsed_seconds:.1f}s, suggesting it was re-acquired.",
                )
            else:
                result.note(
                    f"The lease on {primary_address} is still counting down "
                    f"({primary.valid_lifetime}s remaining)."
                )

    # --- the temporary address ---------------------------------------------
    temporary = _find(observed, temporary_address)
    if temporary is None:
        result.fail(
            "TEMPORARY_ADDRESS_MISSING",
            f"The temporary address {temporary_address} was not created.",
        )
    else:
        if not temporary.is_usable:
            result.fail(
                "TEMPORARY_ADDRESS_NOT_PREFERRED",
                f"{temporary_address} is {temporary.dad_state}, not preferred.",
            )
        if temporary.prefix_length != temporary_prefix_length:
            result.fail(
                "TEMPORARY_PREFIX_WRONG",
                f"{temporary_address} has /{temporary.prefix_length}, expected "
                f"/{temporary_prefix_length}.",
            )
        if temporary.is_dhcp:
            # It must be a distinct, manually originated row, not something the
            # DHCP client has adopted.
            result.fail(
                "TEMPORARY_ADDRESS_IS_DHCP",
                f"{temporary_address} reports DHCP origin; it must be an "
                "independent manual row.",
            )
        else:
            result.note(
                f"{temporary_address} exists independently as "
                f"{temporary.prefix_origin}/{temporary.suffix_origin}."
            )

    # --- everything we promised not to touch --------------------------------
    if before.default_routes != after.default_routes:
        result.fail(
            "DEFAULT_ROUTE_CHANGED",
            "The set of default routes changed during the operation.",
        )
    else:
        result.note("Default routes are unchanged.")

    if before.dns_servers != after.dns_servers:
        result.fail("DNS_CHANGED", "DNS server configuration changed.")
    else:
        result.note("DNS configuration is unchanged.")

    if expected_on_link_prefix not in after.interface_routes:
        result.fail(
            "ON_LINK_ROUTE_MISSING",
            f"No connected route for {expected_on_link_prefix} appeared, so the "
            "temporary address would not reach the management prefix.",
        )
    else:
        result.note(f"A connected route for {expected_on_link_prefix} is present.")

    # Routes that existed before must still exist. New ones are expected: the
    # temporary prefix brings its own.
    lost = set(before.interface_routes) - set(after.interface_routes)
    if lost:
        result.fail(
            "UNRELATED_ROUTE_CHANGED",
            f"Pre-existing route(s) disappeared: {', '.join(sorted(lost))}.",
        )

    # Likewise for addresses. Adding one row must not remove another, and the
    # route comparison above would not notice an address vanishing on its own.
    lost_addresses = set(before.interface_addresses) - set(after.interface_addresses)
    if lost_addresses:
        result.fail(
            "UNRELATED_ADDRESS_REMOVED",
            "Pre-existing address(es) disappeared: "
            + ", ".join(f"{item[0]}/{item[1]}" for item in sorted(lost_addresses))
            + ".",
        )

    # Source selection for destinations that are not on the temporary prefix
    # must not change: adding a management address must not redirect ordinary
    # traffic.
    before_selection = dict(before.source_selection)
    for destination, source in after.source_selection:
        previous = before_selection.get(destination)
        if previous is not None and previous != source:
            result.fail(
                "SOURCE_SELECTION_CHANGED",
                f"Windows now selects {source} toward {destination}, previously "
                f"{previous}.",
            )

    result.preserved = not result.findings
    return result


# --- Gate 2: the orchestrated experiment -----------------------------------
#
# `evaluate_dhcp_coexistence` above is a pure comparison. This is what actually
# runs on a machine, and its whole reason to exist is that the generic
# temporary-address experiment cannot answer the Gate 2 question: it never reads
# the DHCP primary, the default routes, or DNS, so a SUCCESS from it would mean
# less than the capability model claims.
#
# Nothing is created until the baseline proves the interface really is
# DHCP-controlled. An experiment that mutated first and discovered afterwards
# that there was no DHCP row to preserve would have taken a risk for no evidence.

BaselineOutcome = Literal[
    "CAPTURED",
    "IDENTITY_NOT_RESOLVED",
    "PRIMARY_ABSENT",
    "PRIMARY_NOT_DHCP",
    "PRIMARY_NOT_PREFERRED",
    "PRIMARY_LEASE_NOT_FINITE",
    "PRIMARY_AMBIGUOUS",
    "TEMPORARY_ADDRESS_ALREADY_PRESENT",
]

CoexistenceOutcome = Literal[
    "SUCCESS",
    "NOT_AUTHORISED",
    "BASELINE_INCOMPLETE",
    "ADDRESS_CREATE_FAILURE",
    "DAD_DUPLICATE",
    "DAD_TIMEOUT",
    "ROUTE_NOT_ESTABLISHED",
    "COEXISTENCE_VIOLATED",
    "ADDRESS_DELETE_FAILURE",
    "ROLLBACK_INCOMPLETE",
    "BASELINE_NOT_RESTORED",
]


@dataclass(frozen=True)
class DhcpBaseline:
    """Proof, taken before any mutation, that the interface is DHCP-controlled."""

    primary: UnicastAddress
    snapshot: NetworkSnapshot
    interface_index: int
    interface_luid: int


@dataclass
class BaselineCapture:
    outcome: BaselineOutcome
    baseline: DhcpBaseline | None = None
    evidence: list[str] = field(default_factory=list)


def capture_dhcp_baseline(
    *,
    interface_index: int,
    interface_luid: int,
    temporary_address: str,
    rows: Sequence[UnicastAddress],
    snapshot: NetworkSnapshot,
) -> BaselineCapture:
    """Establish the mandatory evidence, or refuse to proceed.

    Every failure here happens *before* anything is created, so a refusal costs
    nothing and an accepted baseline is something later checks can be compared
    against.
    """
    evidence: list[str] = []

    if interface_luid <= 0:
        return BaselineCapture(
            outcome="IDENTITY_NOT_RESOLVED",
            evidence=[
                "The interface has no stable LUID, so a created row could not "
                "later be identified as ours."
            ],
        )

    on_interface = [row for row in rows if row.interface_index == interface_index]

    if any(row.address == temporary_address for row in on_interface):
        # It already exists, so it is not ours, and rollback could not tell.
        return BaselineCapture(
            outcome="TEMPORARY_ADDRESS_ALREADY_PRESENT",
            evidence=[
                f"{temporary_address} is already present on this interface and "
                "would not be ours to remove."
            ],
        )

    dhcp_rows = [row for row in on_interface if row.is_dhcp]
    if not dhcp_rows:
        non_dhcp = [
            f"{row.address} ({row.prefix_origin}/{row.suffix_origin})"
            for row in on_interface
        ]
        return BaselineCapture(
            outcome="PRIMARY_ABSENT" if not on_interface else "PRIMARY_NOT_DHCP",
            evidence=[
                "No DHCP-controlled IPv4 address is present on this interface, "
                "so there is nothing whose preservation could be measured."
            ]
            + ([f"Observed instead: {', '.join(non_dhcp)}."] if non_dhcp else []),
        )
    if len(dhcp_rows) > 1:
        # Which one were we preserving? Refuse rather than pick.
        return BaselineCapture(
            outcome="PRIMARY_AMBIGUOUS",
            evidence=[
                f"{len(dhcp_rows)} DHCP-controlled addresses are present; the "
                "primary to preserve is ambiguous."
            ],
        )

    primary = dhcp_rows[0]
    if not primary.is_usable:
        return BaselineCapture(
            outcome="PRIMARY_NOT_PREFERRED",
            evidence=[
                f"{primary.address} is {primary.dad_state}, so it is not a "
                "settled lease to preserve."
            ],
        )
    if not primary.has_finite_lease:
        return BaselineCapture(
            outcome="PRIMARY_LEASE_NOT_FINITE",
            evidence=[
                f"{primary.address} reports an infinite lifetime, so it is not "
                "behaving as a DHCP lease."
            ],
        )

    evidence.append(
        f"Primary {primary.address}/{primary.prefix_length} is "
        f"{primary.prefix_origin}/{primary.suffix_origin}, {primary.dad_state}, "
        f"lease {primary.valid_lifetime}s remaining."
    )
    evidence.append(
        f"{len(snapshot.default_routes)} default route(s) and "
        f"{len(snapshot.dns_servers)} DNS server(s) captured."
    )
    return BaselineCapture(
        outcome="CAPTURED",
        baseline=DhcpBaseline(
            primary=primary,
            snapshot=snapshot,
            interface_index=interface_index,
            interface_luid=interface_luid,
        ),
        evidence=evidence,
    )


@dataclass
class CoexistenceRunResult:
    outcome: CoexistenceOutcome
    steps: list[tuple[str, str, str]] = field(default_factory=list)
    operation_id: str = ""
    baseline_outcome: BaselineOutcome | None = None
    dad_state: str = "ABSENT"
    findings: list[CoexistenceFinding] = field(default_factory=list)
    evidence: list[str] = field(default_factory=list)
    #: True only when the temporary address is confirmed gone and the DHCP
    #: primary is confirmed unchanged afterwards.
    restored: bool = False
    creates_attempted: int = 0

    def step(self, name: str, status: str, detail: str = "") -> None:
        self.steps.append((status, name, detail))


DAD_TIMEOUT_SECONDS = 15.0
DAD_POLL_SECONDS = 0.5


def run_dhcp_coexistence_experiment(
    *,
    interface_index: int,
    interface_luid: int,
    interface_alias: str,
    temporary_address: str,
    prefix_length: int,
    expected_on_link_prefix: str,
    authority_granted: bool,
    journal,
    read_table,
    read_snapshot,
    create,
    delete,
    sleep=None,
    now=None,
    dad_timeout: float = DAD_TIMEOUT_SECONDS,
    plan_id: str = "gate2",
) -> CoexistenceRunResult:
    """Measure whether a temporary address disturbs a DHCP-controlled primary.

    The Win32 return code is never treated as the answer. Every claim is made by
    re-reading the address table and the surrounding network state.
    """
    import time

    from .journal import OwnedAddress, fingerprint_addresses, new_operation_id
    from .journal import now_iso as _now_iso
    from .windows_unicast import NO_ERROR, describe_error

    sleep = sleep or time.sleep
    result = CoexistenceRunResult(outcome="NOT_AUTHORISED")

    # --- authority ---------------------------------------------------------
    if not authority_granted:
        result.step("authority", "FAIL", "no disposable environment proven")
        result.evidence.append(
            "DHCP coexistence may only run on an adapter proven to belong to a "
            "disposable Recovery Lab environment."
        )
        result.restored = True  # nothing attempted
        return result
    result.step("authority", "PASS", "disposable environment proven")

    # --- baseline: everything that must be true before any mutation --------
    before_snapshot = read_snapshot()
    capture = capture_dhcp_baseline(
        interface_index=interface_index,
        interface_luid=interface_luid,
        temporary_address=temporary_address,
        rows=read_table(),
        snapshot=before_snapshot,
    )
    result.baseline_outcome = capture.outcome
    result.evidence.extend(capture.evidence)
    if capture.outcome != "CAPTURED" or capture.baseline is None:
        result.step("dhcp-baseline", "FAIL", capture.outcome)
        result.outcome = "BASELINE_INCOMPLETE"
        result.restored = True  # nothing was created
        return result
    baseline = capture.baseline
    result.step("dhcp-baseline", "PASS", baseline.primary.address)

    # --- journal before create --------------------------------------------
    operation_id = new_operation_id()
    result.operation_id = operation_id
    owned = OwnedAddress(
        operation_id=operation_id,
        plan_id=plan_id,
        interface_alias=interface_alias,
        interface_index=interface_index,
        interface_luid=interface_luid,
        address=temporary_address,
        prefix_length=prefix_length,
        created_at=_now_iso(),
        state="INTENT_RECORDED",
        previous_state_fingerprint=fingerprint_addresses(
            [(row.address, row.prefix_length) for row in read_table()]
        ),
    )
    journal.record_intent(owned)
    result.step("journal-intent", "PASS", operation_id)

    # --- create ------------------------------------------------------------
    result.creates_attempted += 1
    code = create(
        address=temporary_address,
        prefix_length=prefix_length,
        interface_index=interface_index,
        interface_luid=interface_luid,
    )
    if code != NO_ERROR:
        journal.update_state(operation_id, "COMPLETED", describe_error(code))
        journal.clear(operation_id)
        result.step("create", "FAIL", describe_error(code))
        result.outcome = "ADDRESS_CREATE_FAILURE"
        result.restored = True
        return result
    journal.update_state(operation_id, "ADDRESS_CREATED")
    result.step("create", "PASS")

    def _observe():
        return next(
            (
                row
                for row in read_table()
                if row.address == temporary_address
                and row.interface_index == interface_index
                and row.interface_luid == interface_luid
            ),
            None,
        )

    # --- DAD ---------------------------------------------------------------
    waited = 0.0
    observed = None
    while waited <= dad_timeout:
        observed = _observe()
        state = observed.dad_state if observed else "ABSENT"
        if state not in ("TENTATIVE", "ABSENT"):
            break
        sleep(DAD_POLL_SECONDS)
        waited += DAD_POLL_SECONDS
    result.dad_state = observed.dad_state if observed else "ABSENT"

    if result.dad_state == "DUPLICATE":
        result.step("dad", "FAIL", "another host already holds this address")
        return _finish(result, "DAD_DUPLICATE", owned, journal, delete, read_table,
                       baseline)
    if result.dad_state != "PREFERRED":
        result.step("dad", "FAIL", f"{result.dad_state} after {waited:.1f}s")
        return _finish(result, "DAD_TIMEOUT", owned, journal, delete, read_table,
                       baseline)
    result.step("dad", "PASS", f"preferred after {waited:.1f}s")

    # --- on-link semantics --------------------------------------------------
    if observed is not None and observed.prefix_length != prefix_length:
        result.step(
            "on-link-prefix", "FAIL",
            f"expected /{prefix_length}, observed /{observed.prefix_length}",
        )
        return _finish(result, "ROUTE_NOT_ESTABLISHED", owned, journal, delete,
                       read_table, baseline)
    result.step("on-link-prefix", "PASS", f"/{prefix_length}")

    # --- the actual coexistence question -----------------------------------
    after_snapshot = read_snapshot()
    evaluation = evaluate_dhcp_coexistence(
        primary_address=baseline.primary.address,
        primary_prefix_length=baseline.primary.prefix_length,
        temporary_address=temporary_address,
        temporary_prefix_length=prefix_length,
        baseline_primary=baseline.primary,
        observed=[row for row in read_table() if row.interface_index == interface_index],
        before=baseline.snapshot,
        after=after_snapshot,
        expected_on_link_prefix=expected_on_link_prefix,
        elapsed_seconds=waited,
    )
    result.findings = list(evaluation.findings)
    result.evidence.extend(evaluation.evidence)
    if not evaluation.preserved:
        result.step("coexistence", "FAIL", ", ".join(evaluation.findings))
        return _finish(result, "COEXISTENCE_VIOLATED", owned, journal, delete,
                       read_table, baseline)
    result.step("coexistence", "PASS", "DHCP primary and surroundings preserved")
    journal.update_state(operation_id, "ADDRESS_VERIFIED")

    return _finish(result, "SUCCESS", owned, journal, delete, read_table, baseline)


def _finish(result, outcome, owned, journal, delete, read_table, baseline):
    """Remove exactly the owned row, then prove the baseline is back."""
    from .windows_unicast import NO_ERROR, describe_error

    journal.update_state(owned.operation_id, "ROLLBACK_STARTED")
    code = delete(
        address=owned.address,
        prefix_length=owned.prefix_length,
        interface_index=owned.interface_index,
        interface_luid=owned.interface_luid,
    )
    if code != NO_ERROR:
        result.step("delete", "FAIL", describe_error(code))
        result.outcome = "ADDRESS_DELETE_FAILURE"
        result.restored = False
        return result
    result.step("delete", "PASS")

    rows = read_table()
    still_present = any(
        row.address == owned.address
        and row.interface_index == owned.interface_index
        and row.interface_luid == owned.interface_luid
        for row in rows
    )
    if still_present:
        result.step("rollback-verify", "FAIL", "the temporary address is still present")
        result.outcome = "ROLLBACK_INCOMPLETE"
        result.restored = False
        return result
    result.step("rollback-verify", "PASS", "temporary address removed")

    # The DHCP primary must still be there, and still be a lease, after cleanup.
    primary = next(
        (
            row
            for row in rows
            if row.address == baseline.primary.address
            and row.interface_index == baseline.interface_index
        ),
        None,
    )
    if primary is None or not primary.is_dhcp or not primary.is_usable:
        result.step("baseline-restored", "FAIL", "the DHCP primary did not survive")
        result.outcome = "BASELINE_NOT_RESTORED"
        result.restored = False
        return result
    result.step("baseline-restored", "PASS", f"{primary.address} still DHCP/DHCP")

    journal.update_state(owned.operation_id, "COMPLETED")
    journal.clear(owned.operation_id)
    result.outcome = outcome
    result.restored = True
    return result
