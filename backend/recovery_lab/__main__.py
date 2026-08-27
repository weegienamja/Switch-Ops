"""Development CLI for the Recovery Lab.

    python -m backend.recovery_lab inspect
    python -m backend.recovery_lab restart-check
    python -m backend.recovery_lab experiment --interface "Ethernet 2" \
        --address 192.0.2.250 --prefix-length 24 --allow "Ethernet 2"

    python -m backend.recovery_lab reserve --interface "Ethernet 3"         --address 192.0.2.250 --attested-by "recovery lab harness"         --evidence-reference "gate3-isolated-experiment"
    python -m backend.recovery_lab gate3 --interface "Ethernet 3"         --address 192.0.2.250 --run-id gate3-run-0001

``inspect``, ``restart-check``, ``crash-status`` and ``reservations`` are
read-only and need no elevation. ``experiment``, ``gate3``, ``crash-create``
and successful ``crash-reconcile`` mutate an isolated adapter and require
elevation; without it they report why they refused or the Windows API refuses.
``gate3`` additionally refuses unless a live, in-scope, unexpired reservation
names the exact candidate address, and every one of its refusals happens before
the first create.
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path

from . import windows_unicast as win
from .harness import (
    Baseline,
    assess_restart,
    capture_baseline,
    run_temporary_address_experiment,
)
from datetime import datetime, timezone

from .environment import (
    EnvironmentRegistry,
    WindowsAdapter,
    normalise_guid,
    reconcile_environment,
)
import ipaddress

from .coexistence import NetworkSnapshot, run_dhcp_coexistence_experiment
from .journal import RecoveryJournal
from .provision import LeaseObservation
from .safety import InterfaceFacts, assess_target

#: Harness state lives beside the harness, never in the product data directory.
DEFAULT_JOURNAL = Path(__file__).resolve().parent / "state" / "recovery-journal.json"


def _powershell(script: str) -> object:
    """Read-only PowerShell query returning parsed JSON."""
    completed = subprocess.run(
        ["powershell", "-NoProfile", "-NonInteractive", "-Command", script],
        capture_output=True,
        text=True,
        timeout=60,
    )
    if completed.returncode != 0 or not completed.stdout.strip():
        return []
    try:
        return json.loads(completed.stdout)
    except json.JSONDecodeError:
        return []


def _as_list(value: object) -> list:
    if isinstance(value, list):
        return value
    return [value] if value else []


def gather_interfaces() -> dict[str, InterfaceFacts]:
    """Read adapter facts that decide eligibility."""
    routes = _as_list(
        _powershell(
            "Get-NetRoute -DestinationPrefix '0.0.0.0/0' -ErrorAction SilentlyContinue"
            " | Select-Object ifIndex | ConvertTo-Json -Compress"
        )
    )
    default_route_indexes = {int(item["ifIndex"]) for item in routes if "ifIndex" in item}

    # PowerShell is used only to map alias -> ifIndex. DHCP status comes from
    # the IP Helper table instead: ConvertTo-Json renders PrefixOrigin as an
    # integer, so comparing it to "Dhcp" silently never matched and a DHCP
    # interface was being offered as an experiment candidate.
    addresses = _as_list(
        _powershell(
            "Get-NetIPAddress -AddressFamily IPv4 -ErrorAction SilentlyContinue"
            " | Select-Object InterfaceAlias,ifIndex"
            " | ConvertTo-Json -Compress"
        )
    )
    table = win.read_unicast_table()
    dhcp_indexes = {row.interface_index for row in table if row.is_dhcp}
    # Microsoft resolves a unicast row by LUID in preference to ifIndex, so the
    # LUID is what binds a created address to an exact adapter. It comes from
    # the same table rather than a second query.
    luid_by_index = {row.interface_index: row.interface_luid for row in table}

    facts: dict[str, InterfaceFacts] = {}
    for item in addresses:
        alias = str(item.get("InterfaceAlias", ""))
        index = int(item.get("ifIndex", 0))
        facts[alias] = InterfaceFacts(
            interface_index=index,
            interface_luid=luid_by_index.get(index, 0),
            alias=alias,
            carries_default_route=index in default_route_indexes,
            has_dhcp_lease=index in dhcp_indexes,
        )
    return facts


def gather_baseline() -> Baseline:
    routes = _as_list(
        _powershell(
            "Get-NetRoute -DestinationPrefix '0.0.0.0/0' -ErrorAction SilentlyContinue"
            " | Select-Object ifIndex | ConvertTo-Json -Compress"
        )
    )
    dns = _as_list(
        _powershell(
            "Get-DnsClientServerAddress -AddressFamily IPv4 -ErrorAction SilentlyContinue"
            " | Select-Object -ExpandProperty ServerAddresses | ConvertTo-Json -Compress"
        )
    )
    return capture_baseline(
        addresses=[(row.address, row.prefix_length) for row in win.read_unicast_table()],
        default_route_interfaces=[int(item["ifIndex"]) for item in routes if "ifIndex" in item],
        dns_servers=[str(item) for item in dns],
    )


def gather_network_snapshot(interface_index: int) -> NetworkSnapshot:
    """Capture everything the coexistence check compares, at one instant.

    Read-only. Routes and DNS come from PowerShell; addresses come from the IP
    Helper table so origin and lease state are decoded consistently with the
    rest of the harness.
    """
    routes = _as_list(
        _powershell(
            "Get-NetRoute -AddressFamily IPv4 -ErrorAction SilentlyContinue"
            " | Select-Object ifIndex,DestinationPrefix,NextHop"
            " | ConvertTo-Json -Compress"
        )
    )
    dns = _as_list(
        _powershell(
            "Get-DnsClientServerAddress -AddressFamily IPv4 -ErrorAction SilentlyContinue"
            " | Select-Object -ExpandProperty ServerAddresses | ConvertTo-Json -Compress"
        )
    )

    interface_routes = tuple(
        sorted(
            str(item.get("DestinationPrefix", ""))
            for item in routes
            if int(item.get("ifIndex", -1)) == interface_index
        )
    )
    default_routes = tuple(
        sorted(
            (int(item.get("ifIndex", -1)), str(item.get("NextHop", "")))
            for item in routes
            if str(item.get("DestinationPrefix", "")) == "0.0.0.0/0"
        )
    )
    addresses = tuple(
        sorted(
            (row.address, row.prefix_length)
            for row in win.read_unicast_table()
            if row.interface_index == interface_index
        )
    )
    return NetworkSnapshot(
        interface_addresses=addresses,
        interface_routes=interface_routes,
        default_routes=default_routes,
        dns_servers=tuple(str(item) for item in dns),
    )


def command_inspect() -> int:
    print(f"platform supported : {win.is_supported()}")
    print(f"elevated           : {win.is_elevated()}")
    if not win.is_supported():
        print("\nThe IP Helper unicast APIs are available on Windows only.")
        return 0

    facts = gather_interfaces()
    print("\nInterfaces:")
    for alias, item in sorted(facts.items()):
        flags = []
        if item.carries_default_route:
            flags.append("DEFAULT-ROUTE")
        if item.has_dhcp_lease:
            flags.append("DHCP")
        verdict = "PRODUCTION" if flags else "candidate"
        print(f"  {alias:<45} ifIndex={item.interface_index:<4} "
              f"{verdict:<11} {' '.join(flags)}")

    print("\nIPv4 unicast address table:")
    for row in win.read_unicast_table():
        print(
            f"  {row.address:<16} /{row.prefix_length:<3} if={row.interface_index:<4} "
            f"{row.prefix_origin}/{row.suffix_origin:<12} {row.dad_state:<10} "
            f"{'finite-lease' if row.has_finite_lease else 'infinite'}"
        )
    return 0


def command_restart_check(journal_path: Path) -> int:
    journal = RecoveryJournal(journal_path)
    finding = assess_restart(journal, win.read_unicast_table)
    print(f"disposition: {finding.disposition}")
    print(f"detail     : {finding.detail}")
    for record in finding.records:
        print(
            f"  {record.operation_id} {record.address}/{record.prefix_length} "
            f"on {record.interface_alias} state={record.state}"
        )
    return 0 if finding.disposition != "RECORDED_ROW_PRESENT" else 1


def command_experiment(args: argparse.Namespace) -> int:
    from datetime import datetime, timezone

    from .environment import assess_test_authority

    journal = RecoveryJournal(Path(args.journal))
    facts = gather_interfaces() if win.is_supported() else {}
    interface = facts.get(args.interface)

    # A DHCP-controlled adapter is only eligible when this harness provisioned
    # it. The flag asks the question; provenance answers it.
    dhcp_authority = False
    if getattr(args, "dhcp_coexistence", False):
        registry = EnvironmentRegistry(Path(args.registry))
        adapters = gather_windows_adapters()
        now = datetime.now(timezone.utc)

        # Reconcile first: a record provisioned before Windows assigned an
        # alias has no resolved identity yet, and that is a reason to resolve
        # it, not a reason to refuse. Records are only updated once identity is
        # positively established.
        hostonly = gather_hostonly_guids()
        for environment in registry.all():
            if not environment.has_stable_identity:
                resolved = reconcile_environment(
                    environment, hostonly_guids=hostonly, adapters=adapters, now=now
                )
                if resolved.outcome in ("RECONCILED", "ALREADY_RESOLVED") and resolved.environment:
                    registry.update(resolved.environment)
                    print(f"reconciled {environment.environment_id}")

        authority = assess_test_authority(
            experiment_type="DHCP_COEXISTENCE",
            adapter=find_adapter(adapters, args.interface),
            registry=registry,
            now=now,
            # Authority re-proves the VirtualBox half of the chain live rather
            # than trusting the stored GUID: an interface can be removed and
            # recreated, and a stale record must not keep granting authority.
            hostonly_guids=hostonly,
        )
        dhcp_authority = authority.granted
        print(f"test authority: {authority.provenance}")
        for line in authority.evidence:
            print(f"  {line}")
        for code in authority.blockers:
            print(f"  BLOCKER {code}")

    # Gate 2 asks a different question from the generic primitive experiment,
    # so it runs a different evaluator. The generic path never reads the DHCP
    # primary, the default routes or DNS, so a SUCCESS from it would not mean
    # what the capability model claims DHCP coexistence means.
    if getattr(args, "dhcp_coexistence", False):
        eligibility = assess_target(
            interface=interface,
            address=args.address,
            prefix_length=args.prefix_length,
            allowed_interfaces=args.allow,
            platform_supported=win.is_supported(),
            elevated=win.is_elevated(),
            existing_addresses=[row.address for row in win.read_unicast_table()]
            if win.is_supported() else [],
            dhcp_test_authority=dhcp_authority,
            dhcp_coexistence_requested=True,
        )
        if not eligibility.eligible:
            print("outcome  : NOT_ELIGIBLE")
            print("restored : True")
            print("blockers:")
            for code in eligibility.blockers:
                print(f"  - {code}")
            for line in eligibility.evidence:
                print(f"    {line}")
            return 1

        assert interface is not None
        network = ipaddress.ip_network(
            f"{args.address}/{args.prefix_length}", strict=False
        )
        coexistence = run_dhcp_coexistence_experiment(
            interface_index=interface.interface_index,
            interface_luid=interface.interface_luid,
            interface_alias=interface.alias,
            temporary_address=args.address,
            prefix_length=args.prefix_length,
            expected_on_link_prefix=str(network),
            authority_granted=dhcp_authority,
            journal=journal,
            read_table=win.read_unicast_table,
            read_snapshot=lambda: gather_network_snapshot(interface.interface_index),
            create=win.create_temporary_address,
            delete=win.delete_temporary_address,
        )
        print("evaluator: DHCP_COEXISTENCE")
        print(f"outcome  : {coexistence.outcome}")
        print(f"restored : {coexistence.restored}")
        print(f"baseline : {coexistence.baseline_outcome}")
        print(f"creates  : {coexistence.creates_attempted}")
        if coexistence.operation_id:
            print(f"operation: {coexistence.operation_id}")
        if coexistence.dad_state != "ABSENT":
            print(f"dad      : {coexistence.dad_state}")
        print("steps:")
        for status, name, detail in coexistence.steps:
            print(f"  {status:<8} {name:<18} {detail}")
        if coexistence.findings:
            print("findings:")
            for finding in coexistence.findings:
                print(f"  - {finding}")
        if coexistence.evidence:
            print("evidence:")
            for line in coexistence.evidence:
                print(f"  {line}")
        return 0 if coexistence.outcome == "SUCCESS" else 1

    result = run_temporary_address_experiment(
        interface=interface,
        address=args.address,
        prefix_length=args.prefix_length,
        allowed_interfaces=args.allow,
        journal=journal,
        snapshot=gather_baseline if win.is_supported() else None,
        dhcp_test_authority=dhcp_authority,
    )

    print(f"outcome  : {result.outcome}")
    print(f"restored : {result.restored}")
    if result.operation_id:
        print(f"operation: {result.operation_id}")
    if result.dad_state != "ABSENT":
        print(f"dad      : {result.dad_state} after {result.elapsed_dad_seconds:.1f}s")
    print("steps:")
    for step in result.steps:
        print(f"  {step.status:<8} {step.name:<18} {step.detail}")
    if result.eligibility and result.eligibility.blockers:
        print("blockers:")
        for code in result.eligibility.blockers:
            print(f"  - {code}")
        for line in result.eligibility.evidence:
            print(f"    {line}")
    return 0 if result.outcome == "SUCCESS" else 1


# --- disposable DHCP environment -------------------------------------------

DEFAULT_REGISTRY = Path(__file__).resolve().parent / "state" / "environments.json"


def _lease_probe(interface_guid: str | None) -> LeaseObservation | None:
    """Report the adapter's DHCP row, keyed by GUID.

    Keyed by GUID because the VirtualBox interface name is the Windows
    *description*: matching it against InterfaceAlias never succeeded, which is
    why an environment could be provisioned and still record ifIndex=None.

    The IP Helper table supplies origin, DAD state and lifetime, so provisioning
    can require a genuine DHCP lease rather than merely "an address appeared".
    """
    wanted = normalise_guid(interface_guid)
    if not wanted:
        return None
    adapter = next(
        (item for item in gather_windows_adapters() if item.interface_guid == wanted),
        None,
    )
    if adapter is None:
        return None
    for row in win.read_unicast_table():
        if row.interface_index != adapter.interface_index:
            continue
        return LeaseObservation(
            interface_index=row.interface_index,
            address=row.address,
            alias=adapter.alias,
            prefix_origin=row.prefix_origin,
            suffix_origin=row.suffix_origin,
            dad_state=row.dad_state,
            valid_lifetime=row.valid_lifetime,
        )
    return None


def gather_windows_adapters() -> list[WindowsAdapter]:
    """Every adapter Windows presents, keyed by durable InterfaceGuid."""
    rows = _as_list(
        _powershell(
            "Get-NetAdapter -ErrorAction SilentlyContinue"
            " | Select-Object Name,InterfaceDescription,ifIndex,InterfaceGuid"
            " | ConvertTo-Json -Compress"
        )
    )
    adapters: list[WindowsAdapter] = []
    for item in rows:
        guid = normalise_guid(str(item.get("InterfaceGuid", "")))
        if not guid:
            continue
        adapters.append(
            WindowsAdapter(
                interface_guid=guid,
                alias=str(item.get("Name", "")),
                description=str(item.get("InterfaceDescription", "")),
                interface_index=int(item.get("ifIndex", 0)),
            )
        )
    return adapters


def gather_hostonly_guids() -> dict[str, str]:
    """Map VirtualBox host-only interface name to the GUID VirtualBox assigned.

    VirtualBox is the authority here: it created the interface, so its
    name-to-GUID mapping is what links our record to a Windows adapter.
    """
    vbox = Path(r"C:\Program Files\Oracle\VirtualBox\VBoxManage.exe")
    if not vbox.exists():
        return {}
    try:
        completed = subprocess.run(
            [str(vbox), "list", "hostonlyifs"],
            capture_output=True, text=True, timeout=60,
        )
    except (OSError, subprocess.SubprocessError):
        return {}

    mapping: dict[str, str] = {}
    name: str | None = None
    for line in completed.stdout.splitlines():
        if line.startswith("Name:"):
            name = line.split(":", 1)[1].strip()
        elif line.startswith("GUID:") and name:
            guid = normalise_guid(line.split(":", 1)[1].strip())
            if guid:
                mapping[name] = guid
            name = None
    return mapping


def find_adapter(adapters: list[WindowsAdapter], alias: str) -> WindowsAdapter | None:
    matches = [item for item in adapters if item.alias == alias]
    return matches[0] if len(matches) == 1 else None


DEFAULT_RESERVATIONS = Path(__file__).resolve().parent / "state" / "gate3-reservations.json"


def _resolve_environment(args, *, interface_alias: str):
    """Re-discover which disposable environment an interface belongs to.

    Identity is read live rather than taken from a flag: an argument saying
    "this is the lab adapter" is exactly the assertion Gate 3 refuses to accept
    about an address, and it is no better about an interface.
    """
    from .environment import assess_test_authority

    registry = EnvironmentRegistry(Path(args.registry))
    adapters = gather_windows_adapters()
    now = datetime.now(timezone.utc)
    hostonly = gather_hostonly_guids()

    for environment in registry.all():
        if not environment.has_stable_identity:
            resolved = reconcile_environment(
                environment, hostonly_guids=hostonly, adapters=adapters, now=now
            )
            if resolved.outcome in ("RECONCILED", "ALREADY_RESOLVED") and resolved.environment:
                registry.update(resolved.environment)

    authority = assess_test_authority(
        experiment_type="DHCP_COEXISTENCE",
        adapter=find_adapter(adapters, interface_alias),
        registry=registry,
        now=now,
        hostonly_guids=hostonly,
        live_adapters=adapters,
    )
    return authority, now


def command_reserve(args: argparse.Namespace) -> int:
    """Issue a disposable, time-bounded reservation for one lab address."""
    from .reservation import LabReservationRegistry

    authority, now = _resolve_environment(args, interface_alias=args.interface)
    print(f"test authority: {authority.provenance}")
    for line in authority.evidence:
        print(f"  {line}")
    for code in authority.blockers:
        print(f"  BLOCKER {code}")
    if not authority.granted or not authority.environment_id:
        print("outcome  : ENVIRONMENT_NOT_AUTHORISED")
        print("issued   : False")
        return 1

    network = ipaddress.ip_network(
        f"{args.address}/{args.prefix_length}", strict=False
    )
    reservations = LabReservationRegistry(Path(args.reservations))
    outcome, reservation, evidence = reservations.issue(
        address=args.address,
        target_prefix=str(network),
        environment_id=authority.environment_id,
        attested_by=args.attested_by,
        evidence_reference=args.evidence_reference,
        now=now,
    )
    print(f"outcome  : {outcome}")
    print(f"issued   : {reservation is not None}")
    for line in evidence:
        print(f"  {line}")
    if reservation is not None:
        print(f"reservation: {reservation.reservation_id}")
        print(f"valid until: {reservation.reserved_until}")
        return 0
    return 1


def command_reservations(args: argparse.Namespace) -> int:
    """Read-only listing of what the lab currently claims authority over."""
    from .reservation import LabReservationRegistry

    now = datetime.now(timezone.utc)
    records = LabReservationRegistry(Path(args.reservations)).all()
    if not records:
        print("no Gate 3 reservations recorded")
        return 0
    for item in records:
        expired = datetime.fromisoformat(item.reserved_until) <= now
        state = "released" if item.is_released else ("expired" if expired else "live")
        print(f"{item.reservation_id}  {item.address}/{item.prefix_length}  {state}")
        print(f"  environment : {item.environment_id}")
        print(f"  authority   : {item.authority} ({item.attestor_type})")
        print(f"  until       : {item.reserved_until}")
        if item.operation_binding:
            print(f"  bound to    : {item.operation_binding}")
    return 0


def command_gate3(args: argparse.Namespace) -> int:
    """Run the isolated Gate 3 experiment: authority first, then the mutation."""
    from .gate3 import run_gate3_experiment
    from .reservation import LabReservationRegistry

    journal = RecoveryJournal(Path(args.journal))
    facts = gather_interfaces() if win.is_supported() else {}
    interface = facts.get(args.interface)

    authority, now = _resolve_environment(args, interface_alias=args.interface)
    print(f"test authority: {authority.provenance}")
    for line in authority.evidence:
        print(f"  {line}")
    for code in authority.blockers:
        print(f"  BLOCKER {code}")

    if interface is None or not win.is_supported() or not win.is_elevated():
        # Refusing here keeps the create count at zero for the same reason every
        # other refusal does: nothing may be attempted that cannot be undone.
        print("evaluator: GATE3_RESERVATION_AUTHORITY")
        print("outcome  : ENVIRONMENT_NOT_AUTHORISED")
        print("creates  : 0")
        print("restored : True")
        if interface is None:
            print("  the named interface was not found on this machine")
        if not win.is_elevated():
            print("  Gate 3 requires elevation to create and delete an address")
        return 1

    network = ipaddress.ip_network(
        f"{args.address}/{args.prefix_length}", strict=False
    )
    result = run_gate3_experiment(
        interface_index=interface.interface_index,
        interface_luid=interface.interface_luid,
        interface_alias=interface.alias,
        candidate_address=args.address,
        target_prefix=str(network),
        prefix_length=args.prefix_length,
        environment_id=authority.environment_id,
        environment_authority_granted=authority.granted,
        run_id=args.run_id,
        registry=LabReservationRegistry(Path(args.reservations)),
        journal=journal,
        read_table=win.read_unicast_table,
        read_snapshot=lambda: gather_network_snapshot(interface.interface_index),
        create=win.create_temporary_address,
        delete=win.delete_temporary_address,
        now=now,
    )

    print("evaluator: GATE3_RESERVATION_AUTHORITY")
    print(f"outcome  : {result.outcome}")
    print(f"creates  : {result.creates_attempted}")
    print(f"restored : {result.restored}")
    if result.reservation_id:
        print(f"reservation: {result.reservation_id}")
    if result.operation_id:
        print(f"operation: {result.operation_id}")
    if result.dad_state != "ABSENT":
        print(f"dad      : {result.dad_state}")
    if result.coexistence_outcome:
        print(f"coexistence: {result.coexistence_outcome}")
    print("steps:")
    for status, name, detail in result.steps:
        print(f"  {status:<8} {name:<22} {detail}")
    if result.authority_blockers:
        print("authority blockers:")
        for code in result.authority_blockers:
            print(f"  - {code}")
    if result.evidence:
        print("evidence:")
        for line in result.evidence:
            print(f"  {line}")
    return 0 if result.outcome == "SUCCESS" else 1


def _require_disposable(args):
    """Resolve the disposable environment and the adapter, or refuse.

    Both crash commands go through this. There is no flag that says "trust me"
    and no path that reaches a mutation without it.
    """
    from .environment import normalise_guid

    authority, now = _resolve_environment(args, interface_alias=args.interface)
    print(f"test authority: {authority.provenance}")
    for line in authority.evidence:
        print(f"  {line}")
    for code in authority.blockers:
        print(f"  BLOCKER {code}")

    adapters = gather_windows_adapters()
    adapter = find_adapter(adapters, args.interface)
    guid = normalise_guid(adapter.interface_guid) if adapter else None
    facts = gather_interfaces() if win.is_supported() else {}
    return authority, now, facts.get(args.interface), guid


def command_crash_status(args: argparse.Namespace) -> int:
    """Read-only. What is outstanding, and is its owner still alive?

    This is what the operator runs between the two phases: it proves the first
    process is gone, the address is still there, and the claim is still open,
    without touching any of it.
    """
    from .ownership_lock import owner_process_is_gone_read_only
    from .journal import fingerprint_row
    from .reservation import LabReservationRegistry

    journal = RecoveryJournal(Path(args.journal))
    outstanding = journal.outstanding()
    print(f"outstanding: {len(outstanding)}")
    print("authority  : not adjudicated (diagnostic only)")
    if not outstanding:
        print("nothing is claimed; there is nothing to reconcile")
        return 0

    rows = win.read_unicast_table() if win.is_supported() else []
    reservations = LabReservationRegistry(Path(args.reservations))
    for record in outstanding:
        live_matches = [
            row
            for row in rows
            if row.address == record.address
            and row.interface_luid == record.interface_luid
            and row.interface_index == record.interface_index
            and row.prefix_length == record.prefix_length
        ]
        live = live_matches[0] if len(live_matches) == 1 else None
        gone = owner_process_is_gone_read_only(
            journal.operation_lock_dir, record.operation_id
        )
        print()
        print(f"operation   : {record.operation_id}")
        print(f"  address   : {record.address}/{record.prefix_length}")
        print(f"  state     : {record.state}")
        print(f"  owner     : {'gone' if gone else 'STILL RUNNING'}")
        if len(live_matches) > 1:
            row_state = f"AMBIGUOUS: {len(live_matches)} matching rows"
        elif live is None:
            row_state = "absent under the recorded address/interface/prefix"
        elif not record.has_post_apply_evidence:
            row_state = f"present {live.dad_state}; intent-only identity"
        elif (
            live.creation_timestamp == record.creation_timestamp
            and fingerprint_row(live) == record.post_apply_fingerprint
        ):
            row_state = f"present {live.dad_state}; post-apply evidence matches"
        else:
            row_state = f"present {live.dad_state}; POST-APPLY EVIDENCE DIFFERS"
        print(f"  row       : {row_state}")
        print(f"  evidence  : {'post-apply recorded' if record.has_post_apply_evidence else 'INTENT ONLY'}")
        if record.reservation_id:
            reservation_matches = [
                item
                for item in reservations.all()
                if item.reservation_id == record.reservation_id
            ]
            match = (
                reservation_matches[0]
                if len(reservation_matches) == 1
                else None
            )
            state = "unknown"
            if len(reservation_matches) > 1:
                state = "AMBIGUOUS duplicate reservation ids"
            elif match is not None:
                if match.is_released:
                    state = "released"
                elif match.operation_binding == record.operation_id:
                    state = "bound to this operation/unreleased"
                elif match.operation_binding is None:
                    state = "unbound/unreleased"
                else:
                    state = "BOUND ELSEWHERE/unreleased"
            print(f"  reservation: {state}")
    return 0


def command_crash_create(args: argparse.Namespace) -> int:
    """Phase A. Create one temporary row, then terminate abruptly on purpose."""
    from .crash_experiment import CRASH_EXIT_CODE, run_phase_a
    from .reservation import LabReservationRegistry

    authority, now, interface, guid = _require_disposable(args)
    journal = RecoveryJournal(Path(args.journal))

    if interface is None or not win.is_supported() or not win.is_elevated():
        print("evaluator: CRASH_PHASE_A")
        print("outcome  : ENVIRONMENT_NOT_AUTHORISED")
        print("creates  : 0")
        if interface is None:
            print("  the named interface was not found on this machine")
        if not win.is_elevated():
            print("  creating an address requires elevation")
        return 1

    network = ipaddress.ip_network(
        f"{args.address}/{args.prefix_length}", strict=False
    )
    result = run_phase_a(
        interface_index=interface.interface_index,
        interface_luid=interface.interface_luid,
        interface_alias=interface.alias,
        interface_guid=guid or "",
        candidate_address=args.address,
        target_prefix=str(network),
        prefix_length=args.prefix_length,
        environment_id=authority.environment_id,
        environment_authority_granted=authority.granted,
        run_id=args.run_id,
        registry=LabReservationRegistry(Path(args.reservations)),
        journal=journal,
        read_table=win.read_unicast_table,
        read_snapshot=lambda: gather_network_snapshot(interface.interface_index),
        create=win.create_temporary_address,
        delete=win.delete_temporary_address,
        now=now,
    )
    # Only reached when the run refused before the crash point: a successful
    # Phase A never returns, it exits with CRASH_EXIT_CODE.
    print("evaluator: CRASH_PHASE_A")
    print(f"outcome  : {result.outcome}")
    print(f"creates  : {result.creates_attempted}")
    print(f"deletes  : {result.deletes_attempted}")
    print(f"restored : {result.restored}")
    print("steps:")
    for status, name, detail in result.steps:
        print(f"  {status:<8} {name:<22} {detail}")
    for line in result.evidence:
        print(f"  {line}")
    if result.creates_attempted and not result.restored:
        print()
        print("A temporary address may still be present. Run crash-status, then")
        print(f"crash-reconcile --journal {args.journal}")
    print()
    print(f"(a successful Phase A does not print this; it exits {CRASH_EXIT_CODE})")
    return 1


def command_crash_reconcile(args: argparse.Namespace) -> int:
    """Phase B. A new process recovers ownership from durable state."""
    from .crash_reconcile import reconcile_after_crash
    from .reservation import LabReservationRegistry

    authority, now, interface, guid = _require_disposable(args)
    journal = RecoveryJournal(Path(args.journal))

    if not win.is_supported():
        print("outcome: BLOCKED (not Windows)")
        return 1

    result = reconcile_after_crash(
        journal=journal,
        reservations=LabReservationRegistry(Path(args.reservations)),
        read_table=win.read_unicast_table,
        read_snapshot=lambda: gather_network_snapshot(interface.interface_index),
        delete=win.delete_temporary_address,
        environment_authority_granted=authority.granted,
        environment_id=authority.environment_id,
        live_interface_guid=guid,
        live_interface_index=(interface.interface_index if interface else None),
        live_interface_luid=(interface.interface_luid if interface else None),
        now=now,
    )
    print("evaluator: CRASH_PHASE_B")
    print(f"outcome  : {result.outcome}")
    print(f"deletes  : {result.deletes_attempted}")
    print(f"outstanding after: {result.outstanding_after}")
    print("steps:")
    for status, name, detail in result.steps:
        print(f"  {status:<8} {name:<22} {detail}")
    for item in result.records:
        print()
        print(f"operation : {item.operation_id}")
        print(f"  verdict : {item.verdict}")
        print(f"  deleted : {item.deleted}")
        print(f"  closed  : {item.closed}")
        if item.refusals:
            print(f"  refusals: {', '.join(item.refusals)}")
        for line in item.evidence:
            print(f"    {line}")
    if not win.is_elevated() and result.deletes_attempted:
        print()
        print("note: deletion requires elevation")
    return 0 if result.outcome in ("RECONCILED", "ALREADY_ABSENT", "NOTHING_OUTSTANDING") else 1


def command_reconcile(args: argparse.Namespace) -> int:
    """Establish which Windows adapter each owned environment became."""
    registry = EnvironmentRegistry(Path(args.registry))
    environments = registry.all()
    if not environments:
        print("No disposable environments are recorded.")
        return 0

    adapters = gather_windows_adapters()
    hostonly = gather_hostonly_guids()
    now = datetime.now(timezone.utc)
    failures = 0

    for environment in environments:
        result = reconcile_environment(
            environment,
            hostonly_guids=hostonly,
            adapters=adapters,
            now=now,
        )
        print(f"{environment.environment_id}: {result.outcome}")
        for line in result.evidence:
            print(f"  {line}")
        # The record is only updated once identity is positively established.
        if result.outcome in ("RECONCILED", "ALREADY_RESOLVED") and result.environment:
            registry.update(result.environment)
            print(
                f"  recorded alias={result.environment.observed_alias!r} "
                f"ifIndex={result.environment.interface_index} "
                f"guid={result.environment.interface_guid}"
            )
        else:
            failures += 1
    return 1 if failures else 0


def command_provision(args: argparse.Namespace) -> int:
    from .provision import provision_dhcp_environment

    registry = EnvironmentRegistry(Path(args.registry))
    result = provision_dhcp_environment(
        registry=registry,
        network_cidr=args.network,
        elevated=win.is_elevated(),
        lease_probe=_lease_probe,
        read_hostonly_guids=gather_hostonly_guids,
    )
    print(f"outcome: {result.outcome}")
    for step in result.steps:
        print(f"  {step}")
    if result.environment:
        print(f"environment: {result.environment.environment_id}")
        print(f"virtualbox : {result.environment.hostonly_name}")
        print(f"identity   : {result.environment.interface_guid or 'UNRESOLVED'}")
        print(f"windows    : alias={result.environment.observed_alias or '-'} "
              f"ifIndex={result.environment.interface_index}")
    if result.detail:
        print(f"detail: {result.detail}")
    return 0 if result.outcome == "PROVISIONED" else 1


def command_teardown(args: argparse.Namespace) -> int:
    from .provision import teardown_dhcp_environment

    registry = EnvironmentRegistry(Path(args.registry))
    result = teardown_dhcp_environment(
        registry=registry,
        adapter_alias=args.adapter,
        elevated=win.is_elevated(),
    )
    print(f"outcome: {result.outcome}")
    for step in result.steps:
        print(f"  {step}")
    if result.detail:
        print(f"detail: {result.detail}")
    return 0 if result.outcome == "REMOVED" else 1


def command_environments(args: argparse.Namespace) -> int:
    registry = EnvironmentRegistry(Path(args.registry))
    environments = registry.all()
    if not environments:
        print("No disposable environments are recorded.")
        return 0
    for item in environments:
        identity = item.interface_guid or "UNRESOLVED (run reconcile)"
        alias = item.observed_alias or "-"
        print(f"{item.environment_id}  {item.network_cidr}")
        print(f"    virtualbox : {item.hostonly_name}")
        print(f"    identity   : {identity}")
        print(f"    windows    : alias={alias} ifIndex={item.interface_index}")
        print(f"    created    : {item.created_at}")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="python -m backend.recovery_lab",
        description=(
            "Validate the temporary-address recovery primitive against real "
            "Windows behaviour. Development only; not the SwitchOps executor."
        ),
    )
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("inspect", help="Read-only view of adapters and the address table.")

    restart = sub.add_parser(
        "restart-check",
        help="Compare outstanding journal descriptions with live rows (read-only).",
    )
    restart.add_argument("--journal", default=str(DEFAULT_JOURNAL))

    experiment = sub.add_parser(
        "experiment", help="Create, verify and remove one temporary address."
    )
    experiment.add_argument("--interface", required=True)
    experiment.add_argument("--address", required=True)
    experiment.add_argument("--prefix-length", type=int, default=24)
    experiment.add_argument(
        "--allow",
        action="append",
        default=[],
        help="Interface alias explicitly permitted. Repeatable and required.",
    )
    experiment.add_argument("--journal", default=str(DEFAULT_JOURNAL))
    experiment.add_argument(
        "--registry",
        default=str(DEFAULT_REGISTRY),
        help="Disposable-environment registry used to authorise a DHCP adapter.",
    )
    experiment.add_argument(
        "--dhcp-coexistence",
        action="store_true",
        help=(
            "Measure coexistence with a DHCP-controlled primary. Only permitted "
            "on an adapter this harness provisioned and can still recognise."
        ),
    )

    provision = sub.add_parser(
        "provision", help="Create a disposable DHCP-controlled host-only adapter."
    )
    provision.add_argument("--network", default="192.168.57.0/24")
    provision.add_argument("--registry", default=str(DEFAULT_REGISTRY))

    teardown = sub.add_parser(
        "teardown", help="Remove a disposable adapter this harness created."
    )
    teardown.add_argument("--adapter", required=True)
    teardown.add_argument("--registry", default=str(DEFAULT_REGISTRY))

    environments = sub.add_parser(
        "environments", help="List recorded disposable environments."
    )
    environments.add_argument("--registry", default=str(DEFAULT_REGISTRY))

    reconcile = sub.add_parser(
        "reconcile",
        help="Correlate recorded environments with their current Windows adapters.",
    )
    reconcile.add_argument("--registry", default=str(DEFAULT_REGISTRY))

    reserve = sub.add_parser(
        "reserve",
        help="Issue a disposable Gate 3 reservation for one documentation address.",
    )
    reserve.add_argument("--interface", required=True)
    reserve.add_argument("--address", required=True)
    reserve.add_argument("--prefix-length", type=int, default=24)
    reserve.add_argument("--attested-by", required=True)
    reserve.add_argument("--evidence-reference", required=True)
    reserve.add_argument("--registry", default=str(DEFAULT_REGISTRY))
    reserve.add_argument("--reservations", default=str(DEFAULT_RESERVATIONS))

    reservations_cmd = sub.add_parser(
        "reservations", help="Read-only listing of Gate 3 reservations."
    )
    reservations_cmd.add_argument("--reservations", default=str(DEFAULT_RESERVATIONS))

    gate3 = sub.add_parser(
        "gate3",
        help="Isolated Gate 3 experiment: reservation authority, then the mutation.",
    )
    gate3.add_argument("--interface", required=True)
    gate3.add_argument("--address", required=True)
    gate3.add_argument("--prefix-length", type=int, default=24)
    gate3.add_argument("--run-id", required=True)
    gate3.add_argument("--journal", default=str(DEFAULT_JOURNAL))
    gate3.add_argument("--registry", default=str(DEFAULT_REGISTRY))
    gate3.add_argument("--reservations", default=str(DEFAULT_RESERVATIONS))

    # --- crash / restart ownership (Gate: CRASH_OWNERSHIP_RECONCILIATION) ---
    #
    # Every argument is required and there is no production fallback anywhere:
    # a missing or mistyped flag makes argparse refuse rather than guess.
    crash_create = sub.add_parser(
        "crash-create",
        help=(
            "Phase A: create one temporary RFC 5737 row on a disposable adapter, "
            "then terminate abruptly WITHOUT cleaning up. Leaves an address behind."
        ),
    )
    crash_create.add_argument("--interface", required=True)
    crash_create.add_argument("--address", required=True)
    crash_create.add_argument("--prefix-length", type=int, default=24)
    crash_create.add_argument("--run-id", required=True)
    crash_create.add_argument("--journal", default=str(DEFAULT_JOURNAL))
    crash_create.add_argument("--registry", default=str(DEFAULT_REGISTRY))
    crash_create.add_argument("--reservations", default=str(DEFAULT_RESERVATIONS))

    crash_status = sub.add_parser(
        "crash-status",
        help="Read-only: what is outstanding, and is the owning process gone?",
    )
    crash_status.add_argument("--journal", default=str(DEFAULT_JOURNAL))
    crash_status.add_argument("--reservations", default=str(DEFAULT_RESERVATIONS))

    crash_reconcile = sub.add_parser(
        "crash-reconcile",
        help=(
            "Phase B: a new process proves ownership from durable state and "
            "removes only the exact row it can prove it owns."
        ),
    )
    crash_reconcile.add_argument("--interface", required=True)
    crash_reconcile.add_argument("--journal", default=str(DEFAULT_JOURNAL))
    crash_reconcile.add_argument("--registry", default=str(DEFAULT_REGISTRY))
    crash_reconcile.add_argument("--reservations", default=str(DEFAULT_RESERVATIONS))

    args = parser.parse_args(argv)
    if args.command == "inspect":
        return command_inspect()
    if args.command == "restart-check":
        return command_restart_check(Path(args.journal))
    if args.command == "provision":
        return command_provision(args)
    if args.command == "teardown":
        return command_teardown(args)
    if args.command == "environments":
        return command_environments(args)
    if args.command == "reconcile":
        return command_reconcile(args)
    if args.command == "reserve":
        return command_reserve(args)
    if args.command == "reservations":
        return command_reservations(args)
    if args.command == "gate3":
        return command_gate3(args)
    if args.command == "crash-create":
        return command_crash_create(args)
    if args.command == "crash-status":
        return command_crash_status(args)
    if args.command == "crash-reconcile":
        return command_crash_reconcile(args)
    return command_experiment(args)


if __name__ == "__main__":
    sys.exit(main())
