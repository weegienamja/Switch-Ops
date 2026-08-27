"""Durable ownership record for temporary addresses the harness creates.

Microsoft is explicit that an address created with CreateUnicastIpAddressEntry
"exists only as long as the adapter object exists". It survives the death of the
process that created it. A harness that crashed between create and delete would
therefore leave an address behind with nothing on disk saying who put it there,
and a later run would have no way to tell it apart from an address somebody else
configured deliberately.

So the journal is written *before* the address is created, and only cleared once
the address is confirmed gone. Its job is to answer one question after a crash:
"is this address mine to remove?" -- and to answer *no* whenever the recorded
identity does not match exactly.

Answering *yes* turns out to need more than the intent record. Consider the case
this journal exists to survive: the harness creates 192.0.2.250, dies, the row
disappears for some unrelated reason, and somebody else later creates
192.0.2.250 on the same adapter. Interface LUID, interface index, address and
prefix length are then all identical, and an intent record cannot tell the two
rows apart. Deleting on that evidence would remove a stranger's address.

Windows also reports ``CreationTimeStamp`` for each unicast row. Microsoft calls
it the time the address was created; it is not documented as a unique,
immutable, or cryptographic object identifier. In the narrow same-boot scope it
is nevertheless an important additional discriminator: a later recreation was
observed with a different value. A second durable write therefore happens
immediately after creation, recording the row that was actually observed.
Ownership is proved against that post-apply evidence, never against intent
alone. An operation whose post-apply evidence never landed is unprovable by
design, and unprovable means the reconciler does not touch a present row.

Durability here is scoped to **process death on the same boot**: writes are
flushed and fsynced before the atomic replace, which survives a process being
killed and is what the crash experiment measures. Surviving power loss or an OS
crash is a stronger claim that has not been measured and is not made.
"""

from __future__ import annotations

import ipaddress
import json
import os
import re
import uuid
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Literal

from .ownership_lock import OperationLock, exclusive

JournalState = Literal[
    "INTENT_RECORDED",
    "ADDRESS_CREATED",
    "ADDRESS_VERIFIED",
    "ROLLBACK_STARTED",
    "COMPLETED",
]

JOURNAL_STATES = {
    "INTENT_RECORDED",
    "ADDRESS_CREATED",
    "ADDRESS_VERIFIED",
    "ROLLBACK_STARTED",
    "COMPLETED",
}

#: States from which nothing is owned any more. A record in one of these is
#: history, not a claim.
TERMINAL_STATES = ("COMPLETED",)

#: Bumped whenever the record shape changes. A record written by a version this
#: code does not understand is refused, never guessed at: the whole file exists
#: to authorise deletions.
SCHEMA_VERSION = 3

#: What a later run must match before it will remove anything. Every field is
#: part of the identity; a mismatch in any of them means "not ours".
#:
#: ``creation_timestamp`` makes this materially stronger than a description,
#: because a later same-address recreation was observed to receive a different
#: value. It is still evidence, not a Windows-guaranteed unique object id.
OWNERSHIP_FIELDS = (
    "interface_luid",
    "interface_index",
    "address",
    "prefix_length",
    "creation_timestamp",
)

#: Fields that legitimately change while we own the row, and therefore must not
#: be part of its identity. DAD moves tentative -> preferred on its own; a row
#: that settled after we recorded it is still our row.
MUTABLE_ROW_FIELDS = ("dad_state",)


@dataclass
class OwnedAddress:
    """One temporary address this harness claims to have created."""

    operation_id: str
    plan_id: str
    interface_alias: str
    interface_index: int
    interface_luid: int
    address: str
    prefix_length: int
    created_at: str
    state: JournalState
    #: Fingerprint of the interface's addressing *before* the operation, so a
    #: later run can tell whether the world still looks like it did.
    previous_state_fingerprint: str = ""
    #: Exact DHCP primary captured before mutation. These are restoration
    #: evidence, not part of the temporary row's deletion identity.
    baseline_primary_address: str = ""
    baseline_primary_prefix_length: int = 0
    #: Hash of routes, default routes, DNS and measured source selection before
    #: mutation. It permits comparison after a new process starts without
    #: persisting machine-specific network values in the journal.
    previous_network_fingerprint: str = ""
    notes: list[str] = field(default_factory=list)

    schema_version: int = SCHEMA_VERSION
    #: Which disposable environment this row belongs to, so reconciliation can
    #: re-prove environment ownership rather than trusting the record.
    environment_id: str = ""
    #: The durable Windows identity of the adapter. An alias is a label and an
    #: index is reassignable; this is neither.
    interface_guid: str = ""
    #: The Gate 3 reservation that authorised creating this address, so a
    #: reconciler can close the crashed operation's reservation and no other.
    reservation_id: str = ""
    #: The OS-assigned creation time of the row we actually created. 0 means
    #: "never observed", which can never satisfy an ownership check.
    creation_timestamp: int = 0
    #: Evidence captured *after* the row existed. Empty until then. Ownership
    #: cannot be proven without it.
    post_apply_fingerprint: str = ""
    #: Why the record reached a terminal state, for the audit trail.
    closed_reason: str = ""

    def identity(self) -> tuple:
        return tuple(getattr(self, name) for name in OWNERSHIP_FIELDS)

    @property
    def has_post_apply_evidence(self) -> bool:
        """Was the row observed and recorded after it was created?"""
        return bool(self.post_apply_fingerprint) and self.creation_timestamp > 0

    def matches(self, *, address: str, prefix_length: int, interface_index: int,
                interface_luid: int, creation_timestamp: int | None = None) -> bool:
        """Exact-object match. Deliberately strict.

        ``creation_timestamp`` is optional only so that callers checking "is
        this the kind of row we asked for" keep working. Anything deciding
        whether to *delete* must pass it: without it this is a description, not
        an identity.
        """
        if creation_timestamp is None:
            return (
                self.interface_luid,
                self.interface_index,
                self.address,
                self.prefix_length,
            ) == (interface_luid, interface_index, address, prefix_length)
        return self.identity() == (
            interface_luid,
            interface_index,
            address,
            prefix_length,
            creation_timestamp,
        )


def new_operation_id() -> str:
    return f"recovery-op-{uuid.uuid4().hex[:16]}"


def fingerprint_addresses(addresses: list[tuple[str, int]]) -> str:
    """Stable fingerprint of an interface's addressing."""
    import hashlib

    payload = ";".join(f"{item[0]}/{item[1]}" for item in sorted(addresses))
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:16]


def fingerprint_network_snapshot(snapshot) -> str:
    """Stable comparison value for the non-address parts of a baseline."""
    import hashlib

    payload = json.dumps(
        {
            "interface_routes": sorted(snapshot.interface_routes),
            "default_routes": sorted(
                (int(index), str(next_hop))
                for index, next_hop in snapshot.default_routes
            ),
            # DNS order affects resolver preference, so preserve it.
            "dns_servers": list(snapshot.dns_servers),
            "source_selection": sorted(snapshot.source_selection),
        },
        sort_keys=True,
        separators=(",", ":"),
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:32]


def fingerprint_row(row) -> str:
    """Identity of one live unicast row, as a value that can be compared later.

    Only properties expected to remain stable for the targeted same-boot row go
    in. Windows does not document the timestamp as a unique or cryptographic
    object id, so the hash is corroborating evidence rather than such an id.
    DAD state is excluded on purpose: a row that was tentative when recorded and
    preferred later is the same row in the measured transition.
    """
    import hashlib

    payload = "|".join(
        str(part)
        for part in (
            row.interface_luid,
            row.interface_index,
            row.address,
            row.prefix_length,
            row.creation_timestamp,
            row.prefix_origin,
            row.suffix_origin,
        )
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:32]


class JournalUnreadable(RuntimeError):
    """The journal cannot be trusted. Never downgraded to "nothing is owned"."""


class JournalRecordNotFound(JournalUnreadable):
    """A lifecycle write named an operation the durable journal does not have."""


class JournalTransitionError(JournalUnreadable):
    """A caller attempted to manufacture or replay an impossible state change."""


def _decode_record(record: dict) -> OwnedAddress:
    """Validate a known-schema record before it can participate in authority."""
    allowed = set(OwnedAddress.__dataclass_fields__)
    unknown = set(record) - allowed
    if unknown:
        raise JournalUnreadable(
            "A recovery journal record contains unknown field(s): "
            + ", ".join(sorted(unknown))
            + ". Refusing to guess at an ownership claim."
        )
    try:
        owned = OwnedAddress(**record)
    except (TypeError, ValueError) as error:
        raise JournalUnreadable(
            "A recovery journal record is missing required fields or has an "
            "invalid shape."
        ) from error

    string_fields = (
        "operation_id",
        "plan_id",
        "interface_alias",
        "address",
        "created_at",
        "previous_state_fingerprint",
        "baseline_primary_address",
        "previous_network_fingerprint",
        "environment_id",
        "interface_guid",
        "reservation_id",
        "post_apply_fingerprint",
        "closed_reason",
    )
    if any(not isinstance(getattr(owned, name), str) for name in string_fields):
        raise JournalUnreadable("Journal string fields must actually be strings.")
    if (
        not owned.operation_id
        or len(owned.operation_id) > 200
        or "\x00" in owned.operation_id
    ):
        raise JournalUnreadable("The journal operation id is invalid.")
    if owned.state not in JOURNAL_STATES:
        raise JournalUnreadable(f"Unknown recovery journal state {owned.state!r}.")
    if type(owned.schema_version) is not int or owned.schema_version != SCHEMA_VERSION:
        raise JournalUnreadable("The recovery journal schema version is invalid.")
    if type(owned.interface_index) is not int or owned.interface_index <= 0:
        raise JournalUnreadable("The journal interface index is invalid.")
    if type(owned.interface_luid) is not int or owned.interface_luid <= 0:
        raise JournalUnreadable("The journal interface LUID is invalid.")
    if type(owned.prefix_length) is not int or not 0 < owned.prefix_length <= 32:
        raise JournalUnreadable("The journal prefix length is invalid.")
    if type(owned.creation_timestamp) is not int or owned.creation_timestamp < 0:
        raise JournalUnreadable("The journal creation timestamp is invalid.")
    if (
        type(owned.baseline_primary_prefix_length) is not int
        or not 0 <= owned.baseline_primary_prefix_length <= 32
    ):
        raise JournalUnreadable("The baseline primary prefix length is invalid.")
    if not isinstance(owned.notes, list) or any(
        not isinstance(note, str) for note in owned.notes
    ):
        raise JournalUnreadable("The journal notes field is invalid.")
    try:
        parsed = ipaddress.ip_address(owned.address)
    except ValueError as error:
        raise JournalUnreadable("The journal address is malformed.") from error
    if not isinstance(parsed, ipaddress.IPv4Address):
        raise JournalUnreadable("Only IPv4 recovery journal records are supported.")
    has_baseline_address = bool(owned.baseline_primary_address)
    has_baseline_prefix = owned.baseline_primary_prefix_length > 0
    if has_baseline_address != has_baseline_prefix:
        raise JournalUnreadable(
            "Baseline primary address and prefix must be present together."
        )
    if has_baseline_address:
        try:
            baseline_primary = ipaddress.ip_address(owned.baseline_primary_address)
        except ValueError as error:
            raise JournalUnreadable("The baseline primary address is malformed.") from error
        if not isinstance(baseline_primary, ipaddress.IPv4Address):
            raise JournalUnreadable("The baseline primary must be IPv4.")
    try:
        datetime.fromisoformat(owned.created_at.replace("Z", "+00:00"))
    except (TypeError, ValueError) as error:
        raise JournalUnreadable("The journal creation time is malformed.") from error
    has_timestamp = owned.creation_timestamp > 0
    has_fingerprint = bool(owned.post_apply_fingerprint)
    if has_timestamp != has_fingerprint:
        raise JournalUnreadable(
            "Post-apply timestamp and fingerprint must be present together."
        )
    if owned.state == "INTENT_RECORDED" and has_fingerprint:
        raise JournalUnreadable(
            "An intent-only journal record cannot already contain post-apply "
            "evidence. Creation evidence must enter through record_created."
        )
    if has_fingerprint and not re.fullmatch(
        r"[0-9a-f]{32}", owned.post_apply_fingerprint
    ):
        raise JournalUnreadable("The post-apply fingerprint is malformed.")
    if owned.previous_state_fingerprint and not re.fullmatch(
        r"[0-9a-f]{16}", owned.previous_state_fingerprint
    ):
        raise JournalUnreadable("The pre-operation address fingerprint is malformed.")
    if owned.previous_network_fingerprint and not re.fullmatch(
        r"[0-9a-f]{32}", owned.previous_network_fingerprint
    ):
        raise JournalUnreadable("The pre-operation network fingerprint is malformed.")
    if owned.interface_guid and not re.fullmatch(
        r"\{?[0-9A-Fa-f]{8}(?:-[0-9A-Fa-f]{4}){3}-[0-9A-Fa-f]{12}\}?",
        owned.interface_guid,
    ):
        raise JournalUnreadable("The recorded interface GUID is malformed.")
    return owned


class RecoveryJournal:
    """Small, durable, single-host ownership journal.

    This is harness state, not product state. It is deliberately not wired into
    the SwitchOps runtime: the product has no execution authority to journal.
    """

    def __init__(self, path: Path) -> None:
        self.path = Path(path)

    # --- locations ---------------------------------------------------------

    @property
    def lock_path(self) -> Path:
        """Serialises read-modify-write so concurrent runs cannot lose records."""
        return self.path.with_name(self.path.name + ".lock")

    @property
    def operation_lock_dir(self) -> Path:
        """One lock per operation, held for as long as it owns an address."""
        return self.path.with_name(self.path.stem + "-locks")

    @property
    def reconciliation_lock_path(self) -> Path:
        """Serialises Phase B so two reconcilers cannot both adjudicate."""
        return self.path.with_name(self.path.name + ".reconcile.lock")

    def operation_lock(self, operation_id: str) -> OperationLock:
        return OperationLock(self.operation_lock_dir, operation_id)

    # --- persistence -------------------------------------------------------

    def _read(self) -> list[dict]:
        if not self.path.exists():
            return []
        try:
            payload = json.loads(self.path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError) as error:
            # A corrupt journal must not be silently treated as "nothing owned":
            # that is the one misreading that could authorise creating a second
            # address while the first is still outstanding.
            raise JournalUnreadable(
                f"The recovery journal at {self.path.name} is unreadable. Resolve "
                "it manually before running another experiment."
            ) from error
        if not isinstance(payload, list) or any(
            not isinstance(record, dict) for record in payload
        ):
            raise JournalUnreadable(
                f"The recovery journal at {self.path.name} has an invalid shape. "
                "Resolve it manually before running another experiment."
            )
        operation_ids: set[str] = set()
        for record in payload:
            version = record.get("schema_version")
            if version != SCHEMA_VERSION:
                raise JournalUnreadable(
                    f"A record in {self.path.name} declares schema version "
                    f"{version!r}, but this harness understands {SCHEMA_VERSION}. "
                    "Refusing to reinterpret an ownership claim."
                )
            owned = _decode_record(record)
            if owned.operation_id in operation_ids:
                raise JournalUnreadable(
                    f"Operation {owned.operation_id} appears more than once in "
                    f"{self.path.name}; ownership is ambiguous."
                )
            operation_ids.add(owned.operation_id)
        return payload

    def _write(self, records: list[dict]) -> None:
        # Unique temp name: a shared one lets two writers scribble over each
        # other's partial file and then publish the result atomically, which is
        # worse than not being atomic at all.
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temporary = self.path.with_name(
            f"{self.path.name}.{os.getpid()}.{uuid.uuid4().hex[:8]}.tmp"
        )
        try:
            with open(temporary, "w", encoding="utf-8") as handle:
                json.dump(records, handle, indent=2)
                handle.flush()
                # Past the process's buffers and into the OS. That is the level
                # of durability this design claims and the crash experiment
                # measures: survives the process dying, not the machine dying.
                os.fsync(handle.fileno())
            os.replace(temporary, self.path)
        except BaseException:
            try:
                temporary.unlink(missing_ok=True)
            except OSError:
                pass
            raise

    def _mutate(self, change) -> None:
        """Read, change, write -- with nobody else in the middle."""
        with exclusive(self.lock_path):
            records = self._read()
            change(records)
            self._write(records)

    # --- lifecycle ---------------------------------------------------------

    def record_intent(self, owned: OwnedAddress) -> None:
        """Persist the claim *before* the address exists."""
        owned.schema_version = SCHEMA_VERSION
        if (
            owned.state != "INTENT_RECORDED"
            or owned.creation_timestamp != 0
            or owned.post_apply_fingerprint
            or owned.closed_reason
        ):
            raise JournalTransitionError(
                "record_intent accepts only a clean INTENT_RECORDED record. "
                "Post-apply evidence must be written by record_created."
            )
        _decode_record(asdict(owned))

        def change(records: list[dict]) -> None:
            if any(item.get("operation_id") == owned.operation_id for item in records):
                # Reusing an operation id would let a new run inherit an old
                # record's evidence, which is the replay this design refuses.
                raise JournalUnreadable(
                    f"Operation {owned.operation_id} is already recorded. An "
                    "operation id may never be reused."
                )
            records.append(asdict(owned))

        self._mutate(change)

    def record_created(
        self,
        operation_id: str,
        *,
        creation_timestamp: int,
        post_apply_fingerprint: str,
        note: str | None = None,
    ) -> None:
        """Record what the row *is*, immediately after creating it.

        This is the write that makes a crash reconcilable. Until it lands, the
        journal knows an address was intended but not which object resulted, and
        the reconciler will refuse to delete anything on that basis.
        """
        if creation_timestamp <= 0 or not post_apply_fingerprint:
            raise ValueError(
                "Post-apply evidence needs a real creation timestamp and "
                "fingerprint; recording a placeholder would fake ownership proof."
            )

        def change(records: list[dict]) -> None:
            record = _find_record(records, operation_id)
            if record.get("state") != "INTENT_RECORDED":
                raise JournalTransitionError(
                    f"Operation {operation_id} cannot record creation evidence "
                    f"from state {record.get('state')!r}."
                )
            record["state"] = "ADDRESS_CREATED"
            record["creation_timestamp"] = creation_timestamp
            record["post_apply_fingerprint"] = post_apply_fingerprint
            if note:
                record.setdefault("notes", []).append(note)
            _decode_record(record)

        self._mutate(change)

    def update_state(self, operation_id: str, state: JournalState,
                     note: str | None = None) -> None:
        if state not in JOURNAL_STATES:
            raise JournalTransitionError(f"Unknown recovery journal state {state!r}.")

        def change(records: list[dict]) -> None:
            record = _find_record(records, operation_id)
            previous = record.get("state")
            allowed = {
                "INTENT_RECORDED": {
                    "ADDRESS_CREATED",
                    "ROLLBACK_STARTED",
                    "COMPLETED",
                },
                "ADDRESS_CREATED": {
                    "ADDRESS_VERIFIED",
                    "ROLLBACK_STARTED",
                    "COMPLETED",
                },
                "ADDRESS_VERIFIED": {"ROLLBACK_STARTED", "COMPLETED"},
                "ROLLBACK_STARTED": {"COMPLETED"},
                "COMPLETED": set(),
            }
            if state != previous and state not in allowed.get(previous, set()):
                raise JournalTransitionError(
                    f"Operation {operation_id} cannot move from {previous!r} "
                    f"to {state!r}."
                )
            record["state"] = state
            if note:
                record.setdefault("notes", []).append(note)
            _decode_record(record)

        self._mutate(change)

    def close(self, operation_id: str, reason: str) -> None:
        """Mark an operation finished, keeping the record.

        Kept rather than deleted so that "this address was ours and is gone" is
        still on disk afterwards. `clear` remains for callers that want the row
        removed entirely.
        """
        if not isinstance(reason, str) or not reason.strip():
            raise JournalTransitionError("Closing a journal record requires a reason.")

        def change(records: list[dict]) -> None:
            record = _find_record(records, operation_id)
            if record.get("state") == "COMPLETED":
                return
            record["state"] = "COMPLETED"
            record["closed_reason"] = reason
            _decode_record(record)

        self._mutate(change)

    def clear(self, operation_id: str) -> None:
        """Remove a record only once its address is confirmed gone."""
        def change(records: list[dict]) -> None:
            record = _find_record(records, operation_id)
            if record.get("state") != "COMPLETED":
                raise JournalTransitionError(
                    f"Operation {operation_id} cannot be removed while state is "
                    f"{record.get('state')!r}."
                )
            records.remove(record)

        self._mutate(change)

    # --- reads -------------------------------------------------------------

    def all(self) -> list[OwnedAddress]:
        return [_decode_record(record) for record in self._read()]

    def get(self, operation_id: str) -> OwnedAddress | None:
        """Return one current record from a fresh atomic journal read."""
        return next(
            (
                _decode_record(record)
                for record in self._read()
                if record.get("operation_id") == operation_id
            ),
            None,
        )

    def outstanding(self) -> list[OwnedAddress]:
        """Records that did not reach a terminal state, newest first."""
        found = [
            _decode_record(record)
            for record in self._read()
            if record.get("state") not in TERMINAL_STATES
        ]
        return sorted(found, key=lambda item: item.created_at, reverse=True)


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _find_record(records: list[dict], operation_id: str) -> dict:
    matches = [
        record for record in records if record.get("operation_id") == operation_id
    ]
    if len(matches) != 1:
        raise JournalRecordNotFound(
            f"Operation {operation_id} is not present exactly once in the journal."
        )
    return matches[0]
