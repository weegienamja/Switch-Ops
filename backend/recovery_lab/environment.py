"""Provenance for a disposable test environment this stage created itself.

The DHCP-coexistence experiment needs something the safety model has always
refused: an interface whose primary address is controlled by DHCP. The wrong way
to allow that is to relax the global guard to "DHCP interfaces are fine now",
which would make the operator's production adapter eligible the moment somebody
passed the right ``--allow``.

Instead an interface is eligible only when this harness created it and can still
*prove* which Windows adapter that became.

The proof runs on interface GUID. VirtualBox reports the GUID of a host-only
interface it created, and Windows reports the same value as ``InterfaceGuid`` on
the corresponding adapter (synthetic example):

    VBoxManage: GUID 11111111-2222-4333-8444-555555555555
    Windows:    InterfaceGuid {11111111-2222-4333-8444-555555555555}

That chain -- we created VirtualBox interface X, VirtualBox says X is GUID G,
Windows says G is this adapter -- is ownership evidence. A matching *name*,
description, subnet, or absent default route is not: those are properties an
unrelated adapter can share.

The original schema stored a display name as identity, which is what made this
correlation fail: the VirtualBox interface name is the Windows
*InterfaceDescription*, never the *InterfaceAlias*.
"""

from __future__ import annotations

import json
import os
import re
import uuid
from dataclasses import asdict, dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Literal, Mapping, Sequence

SCHEMA_VERSION = 2
OWNED_CREATOR = "backend.recovery_lab"

ExperimentType = Literal["EPHEMERAL_PRIMITIVE", "DHCP_COEXISTENCE"]

InterfaceProvenance = Literal[
    #: Created by this harness, and positively correlated to a live adapter.
    "DISPOSABLE_DHCP_ENVIRONMENT",
    #: Anything else, including adapters that merely look disposable.
    "PREEXISTING",
]

#: A provisioning record older than this is not trusted to still describe the
#: machine: adapters get renumbered, removed, and reused.
MAX_ENVIRONMENT_AGE = timedelta(days=7)

AuthorityBlocker = Literal[
    "ENVIRONMENT_NOT_OWNED",
    "ENVIRONMENT_IDENTITY_NOT_RESOLVED",
    "ENVIRONMENT_IDENTITY_AMBIGUOUS",
    "ENVIRONMENT_ADAPTER_CHANGED",
    "ENVIRONMENT_RECORD_STALE",
    "EXPERIMENT_TYPE_NOT_AUTHORISED",
]


def normalise_guid(value: str | None) -> str | None:
    """Reduce a GUID to a comparable form.

    VirtualBox prints a bare lowercase value and Windows commonly prints the
    same value braced and uppercase. Comparing them raw silently never matches.
    """
    if not value:
        return None
    cleaned = value.strip().strip("{}").lower()
    if not re.fullmatch(r"[0-9a-f]{8}(-[0-9a-f]{4}){3}-[0-9a-f]{12}", cleaned):
        return None
    return cleaned


@dataclass
class WindowsAdapter:
    """One adapter as Windows currently presents it.

    Only ``interface_guid`` is identity. Alias and index are observations that
    change with PnP events, renaming, and re-enumeration.
    """

    interface_guid: str
    alias: str
    description: str
    interface_index: int


@dataclass
class DisposableEnvironment:
    """A disposable adapter this harness provisioned, and how to recognise it."""

    environment_id: str
    #: VirtualBox host-only interface name. Needed for teardown, and equal to
    #: the Windows InterfaceDescription -- but it is a name, not identity.
    hostonly_name: str
    network_cidr: str
    created_at: str
    schema_version: int = SCHEMA_VERSION
    created_by: str = OWNED_CREATOR
    #: The durable link between "what we created" and "what Windows shows".
    #: None on a v1 record, or before reconciliation has run.
    interface_guid: str | None = None
    #: Observed properties, refreshed by reconciliation. Never identity.
    observed_alias: str | None = None
    interface_index: int | None = None
    observed_at: str | None = None
    notes: list[str] = field(default_factory=list)

    def age(self, now: datetime) -> timedelta:
        created = datetime.fromisoformat(self.created_at.replace("Z", "+00:00"))
        return now - created

    @property
    def has_stable_identity(self) -> bool:
        return normalise_guid(self.interface_guid) is not None


def new_environment_id() -> str:
    return f"recovery-env-{uuid.uuid4().hex[:12]}"


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _migrate(record: dict) -> dict:
    """Bring a stored record up to the current schema without inventing facts.

    A v1 record kept the VirtualBox interface name in ``adapter_alias``, which
    was never a Windows alias. It is moved to ``hostonly_name`` and the alias is
    dropped rather than reinterpreted: the record simply did not know the
    Windows identity, and reconciliation is what establishes it.
    """
    migrated = dict(record)
    version = int(migrated.pop("schema_version", 1) or 1)
    if version > SCHEMA_VERSION:
        raise RuntimeError(
            f"Environment schema {version} is newer than supported schema "
            f"{SCHEMA_VERSION}; ownership cannot be interpreted safely."
        )
    legacy_alias = migrated.pop("adapter_alias", None)
    if version < 2:
        migrated.setdefault("hostonly_name", legacy_alias or "")
        migrated["interface_guid"] = None
        migrated["observed_alias"] = None
        migrated["observed_at"] = None
        migrated.setdefault("notes", []).append(
            "migrated from schema 1; Windows identity requires reconciliation"
        )
    # Missing provenance is not silently replaced with our own marker. New
    # in-memory records receive OWNED_CREATOR from the dataclass default, while
    # an old or hand-written record without it remains untrusted.
    migrated.setdefault("created_by", "")
    migrated["schema_version"] = SCHEMA_VERSION
    allowed = set(DisposableEnvironment.__dataclass_fields__)
    return {key: value for key, value in migrated.items() if key in allowed}


class EnvironmentRegistry:
    """Records disposable environments so they can be recognised and removed.

    Kept separate from the operation journal: one tracks *adapters we created*,
    the other tracks *addresses we created*.
    """

    def __init__(self, path: Path) -> None:
        self.path = path
        self.path.parent.mkdir(parents=True, exist_ok=True)

    def _read(self) -> list[dict]:
        if not self.path.exists():
            return []
        try:
            payload = json.loads(self.path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            raise RuntimeError(
                f"The environment registry at {self.path.name} is unreadable. "
                "Resolve it manually before provisioning or tearing down."
            )
        if not isinstance(payload, list) or any(
            not isinstance(record, dict) for record in payload
        ):
            raise RuntimeError(
                f"The environment registry at {self.path.name} has an invalid "
                "shape. Resolve it manually before provisioning or tearing down."
            )
        return payload

    def _write(self, records: list[dict]) -> None:
        temporary = self.path.with_suffix(".tmp")
        temporary.write_text(json.dumps(records, indent=2), encoding="utf-8")
        os.replace(temporary, self.path)

    def record(self, environment: DisposableEnvironment) -> None:
        records = self._read()
        records.append(asdict(environment))
        self._write(records)

    def all(self) -> list[DisposableEnvironment]:
        return [DisposableEnvironment(**_migrate(record)) for record in self._read()]

    def find_by_id(self, environment_id: str) -> DisposableEnvironment | None:
        return next(
            (item for item in self.all() if item.environment_id == environment_id), None
        )

    def find_by_hostonly_name(self, name: str) -> DisposableEnvironment | None:
        matches = [item for item in self.all() if item.hostonly_name == name]
        return matches[0] if len(matches) == 1 else None

    def find_all_by_guid(
        self, interface_guid: str | None
    ) -> list[DisposableEnvironment]:
        """Return every record claiming one identity.

        More than one record is corruption or incomplete cleanup, never a
        reason to choose whichever happened to be stored first.
        """
        wanted = normalise_guid(interface_guid)
        if wanted is None:
            return []
        return [
            item
            for item in self.all()
            if normalise_guid(item.interface_guid) == wanted
        ]

    def find_by_guid(self, interface_guid: str | None) -> DisposableEnvironment | None:
        """Look up one unambiguous durable identity.

        Duplicate claims fail closed. Callers that need to explain the
        ambiguity can use :meth:`find_all_by_guid`.
        """
        matches = self.find_all_by_guid(interface_guid)
        return matches[0] if len(matches) == 1 else None

    def update(self, environment: DisposableEnvironment) -> None:
        records = [
            asdict(environment)
            if record.get("environment_id") == environment.environment_id
            else record
            for record in self._read()
        ]
        self._write(records)

    def remove(self, environment_id: str) -> None:
        self._write(
            [
                record
                for record in self._read()
                if record.get("environment_id") != environment_id
            ]
        )


# --- reconciliation --------------------------------------------------------

ReconcileOutcome = Literal[
    "RECONCILED",
    "ALREADY_RESOLVED",
    "ENVIRONMENT_NOT_OWNED",
    "ENVIRONMENT_ADAPTER_CHANGED",
    "ENVIRONMENT_IDENTITY_AMBIGUOUS",
    "ENVIRONMENT_IDENTITY_NOT_RESOLVED",
]


@dataclass(frozen=True)
class ReconcileResult:
    outcome: ReconcileOutcome
    environment: DisposableEnvironment | None = None
    evidence: list[str] = field(default_factory=list)


def reconcile_environment(
    environment: DisposableEnvironment,
    *,
    hostonly_guids: dict[str, str],
    adapters: Sequence[WindowsAdapter],
    now: datetime,
) -> ReconcileResult:
    """Establish which Windows adapter an owned environment became.

    ``hostonly_guids`` maps VirtualBox host-only interface name to its GUID, as
    reported by VirtualBox. That is what makes this ownership evidence rather
    than name matching: the authority for "interface X is GUID G" is the same
    component that created X.
    """
    evidence: list[str] = []

    if environment.created_by != OWNED_CREATOR:
        return ReconcileResult(
            outcome="ENVIRONMENT_NOT_OWNED",
            evidence=[
                "The record was not created by backend.recovery_lab; it cannot "
                "establish deletion or experiment authority."
            ],
        )

    recorded_guid = normalise_guid(environment.interface_guid)
    reported_guid = normalise_guid(hostonly_guids.get(environment.hostonly_name))
    if reported_guid is None:
        # Reconciliation is deliberately contemporaneous. A once-valid record
        # does not make a newly reused VirtualBox display name ours.
        return ReconcileResult(
            outcome="ENVIRONMENT_ADAPTER_CHANGED",
            evidence=[
                f"VirtualBox does not report a host-only interface named "
                f"{environment.hostonly_name!r}, so the recorded environment "
                "cannot be linked to a live adapter."
            ],
        )
    evidence.append(
        f"VirtualBox reports {environment.hostonly_name!r} as GUID {reported_guid}."
    )

    if recorded_guid is not None and recorded_guid != reported_guid:
        return ReconcileResult(
            outcome="ENVIRONMENT_ADAPTER_CHANGED",
            evidence=evidence
            + [
                f"The registry records GUID {recorded_guid}; the VirtualBox name "
                "now identifies a different adapter."
            ],
        )
    guid = recorded_guid or reported_guid

    matches = [
        adapter
        for adapter in adapters
        if normalise_guid(adapter.interface_guid) == guid
    ]
    if not matches:
        return ReconcileResult(
            outcome="ENVIRONMENT_ADAPTER_CHANGED",
            evidence=evidence
            + [f"No Windows adapter currently reports InterfaceGuid {guid}."],
        )
    if len(matches) > 1:
        # Should be impossible; if it happens we do not get to choose.
        return ReconcileResult(
            outcome="ENVIRONMENT_IDENTITY_AMBIGUOUS",
            evidence=evidence
            + [f"{len(matches)} Windows adapters report InterfaceGuid {guid}."],
        )

    adapter = matches[0]
    evidence.append(
        f"Windows adapter {adapter.alias!r} (ifIndex {adapter.interface_index}) "
        f"reports the same GUID, so it is the interface this harness created."
    )

    already = (
        normalise_guid(environment.interface_guid) == guid
        and environment.observed_alias == adapter.alias
        and environment.interface_index == adapter.interface_index
    )
    updated = DisposableEnvironment(
        **{
            **asdict(environment),
            "interface_guid": guid,
            "observed_alias": adapter.alias,
            "interface_index": adapter.interface_index,
            "observed_at": now.isoformat().replace("+00:00", "Z"),
        }
    )
    return ReconcileResult(
        outcome="ALREADY_RESOLVED" if already else "RECONCILED",
        environment=updated,
        evidence=evidence,
    )


# --- authority -------------------------------------------------------------


@dataclass(frozen=True)
class ExperimentAuthority:
    """Whether a DHCP-controlled interface may be used for one experiment."""

    granted: bool
    provenance: InterfaceProvenance
    environment_id: str | None = None
    blockers: list[AuthorityBlocker] = field(default_factory=list)
    evidence: list[str] = field(default_factory=list)


def assess_test_authority(
    *,
    experiment_type: ExperimentType,
    adapter: WindowsAdapter | None,
    registry: EnvironmentRegistry,
    now: datetime,
    hostonly_guids: Mapping[str, str] | None = None,
) -> ExperimentAuthority:
    """Decide whether a live adapter belongs to a disposable environment.

    Correlation is by interface GUID only. The adapter's alias, description,
    subnet, DHCP server, and absence of a default route are all things an
    unrelated adapter could present, so none of them grant authority.
    """
    blockers: list[AuthorityBlocker] = []
    evidence: list[str] = []

    if experiment_type != "DHCP_COEXISTENCE":
        # Only this experiment has any business touching a DHCP interface.
        blockers.append("EXPERIMENT_TYPE_NOT_AUTHORISED")
        evidence.append(
            f"{experiment_type} does not authorise the use of a DHCP-controlled "
            "interface."
        )

    if adapter is None:
        blockers.append("ENVIRONMENT_IDENTITY_NOT_RESOLVED")
        evidence.append("The named interface was not found on this machine.")
        return ExperimentAuthority(
            granted=False, provenance="PREEXISTING", blockers=blockers, evidence=evidence
        )

    guid = normalise_guid(adapter.interface_guid)
    if guid is None:
        blockers.append("ENVIRONMENT_IDENTITY_NOT_RESOLVED")
        evidence.append(
            f"{adapter.alias!r} does not report a usable InterfaceGuid, so it "
            "cannot be correlated to a recorded environment."
        )
        return ExperimentAuthority(
            granted=False, provenance="PREEXISTING", blockers=blockers, evidence=evidence
        )

    matches = registry.find_all_by_guid(guid)
    if len(matches) > 1:
        blockers.append("ENVIRONMENT_IDENTITY_AMBIGUOUS")
        evidence.append(
            f"{len(matches)} environment records claim GUID {guid}; ownership "
            "must be reconciled instead of selecting one."
        )
        return ExperimentAuthority(
            granted=False, provenance="PREEXISTING", blockers=blockers, evidence=evidence
        )

    environment = matches[0] if matches else None
    if environment is None:
        unresolved = [item for item in registry.all() if not item.has_stable_identity]
        if unresolved:
            # A record exists but has never been correlated. That is a reason to
            # reconcile, not a reason to proceed.
            blockers.append("ENVIRONMENT_IDENTITY_NOT_RESOLVED")
            evidence.append(
                f"{len(unresolved)} recorded environment(s) have no resolved Windows "
                "identity. Run 'reconcile' before this experiment."
            )
        else:
            blockers.append("ENVIRONMENT_NOT_OWNED")
            evidence.append(
                f"{adapter.alias!r} (GUID {guid}) matches no environment this "
                "harness created, so it is treated as the operator's own."
            )
        return ExperimentAuthority(
            granted=False, provenance="PREEXISTING", blockers=blockers, evidence=evidence
        )

    if environment.created_by != OWNED_CREATOR:
        blockers.append("ENVIRONMENT_NOT_OWNED")
        evidence.append(
            "The matching registry row does not carry this harness's creator "
            "provenance, so it cannot grant authority."
        )
        return ExperimentAuthority(
            granted=False,
            provenance="PREEXISTING",
            environment_id=environment.environment_id,
            blockers=blockers,
            evidence=evidence,
        )

    live_guid = normalise_guid(
        (hostonly_guids or {}).get(environment.hostonly_name)
    )
    if live_guid != guid:
        blockers.append("ENVIRONMENT_ADAPTER_CHANGED")
        if live_guid is None:
            evidence.append(
                f"VirtualBox no longer reports {environment.hostonly_name!r}; "
                "the ownership chain cannot be re-proven."
            )
        else:
            evidence.append(
                f"VirtualBox now reports GUID {live_guid} for "
                f"{environment.hostonly_name!r}, not the recorded GUID {guid}."
            )
        return ExperimentAuthority(
            granted=False,
            provenance="PREEXISTING",
            environment_id=environment.environment_id,
            blockers=blockers,
            evidence=evidence,
        )

    evidence.append(
        f"{adapter.alias!r} (GUID {guid}) is environment "
        f"{environment.environment_id} on {environment.network_cidr}."
    )

    try:
        age = environment.age(now)
    except (TypeError, ValueError):
        age = MAX_ENVIRONMENT_AGE + timedelta(seconds=1)
        evidence.append("The environment creation timestamp is invalid.")

    if age > MAX_ENVIRONMENT_AGE or age < timedelta(0):
        blockers.append("ENVIRONMENT_RECORD_STALE")
        evidence.append(
            "The environment timestamp is stale or in the future and needs "
            "re-provisioning before it can authorise an experiment."
        )

    if (
        environment.interface_index is not None
        and environment.interface_index != adapter.interface_index
    ):
        # The GUID still matches, so it is the same adapter; the index simply
        # moved. Worth recording, not worth refusing.
        evidence.append(
            f"ifIndex moved from {environment.interface_index} to "
            f"{adapter.interface_index}; identity is unchanged."
        )

    return ExperimentAuthority(
        granted=not blockers,
        provenance="DISPOSABLE_DHCP_ENVIRONMENT" if not blockers else "PREEXISTING",
        environment_id=environment.environment_id,
        blockers=blockers,
        evidence=evidence,
    )
