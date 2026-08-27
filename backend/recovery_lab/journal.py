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
"""

from __future__ import annotations

import json
import os
import uuid
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Literal

JournalState = Literal[
    "INTENT_RECORDED",
    "ADDRESS_CREATED",
    "ADDRESS_VERIFIED",
    "ROLLBACK_STARTED",
    "COMPLETED",
]

#: What a later run must match before it will remove anything. Every field is
#: part of the identity; a mismatch in any of them means "not ours".
OWNERSHIP_FIELDS = (
    "interface_luid",
    "interface_index",
    "address",
    "prefix_length",
)


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
    notes: list[str] = field(default_factory=list)

    def identity(self) -> tuple:
        return tuple(getattr(self, name) for name in OWNERSHIP_FIELDS)

    def matches(self, *, address: str, prefix_length: int, interface_index: int,
                interface_luid: int) -> bool:
        """Exact-object match. Deliberately strict."""
        return self.identity() == (
            interface_luid,
            interface_index,
            address,
            prefix_length,
        )


def new_operation_id() -> str:
    return f"recovery-op-{uuid.uuid4().hex[:16]}"


def fingerprint_addresses(addresses: list[tuple[str, int]]) -> str:
    """Stable fingerprint of an interface's addressing."""
    import hashlib

    payload = ";".join(f"{item[0]}/{item[1]}" for item in sorted(addresses))
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:16]


class RecoveryJournal:
    """Append-only-ish JSON journal. Small on purpose.

    This is harness state, not product state. It is deliberately not wired into
    the SwitchOps runtime: the product has no execution authority to journal.
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
            # A corrupt journal must not be silently treated as "nothing owned".
            raise RuntimeError(
                f"The recovery journal at {self.path.name} is unreadable. Resolve "
                "it manually before running another experiment."
            )
        if not isinstance(payload, list) or any(
            not isinstance(record, dict) for record in payload
        ):
            raise RuntimeError(
                f"The recovery journal at {self.path.name} has an invalid shape. "
                "Resolve it manually before running another experiment."
            )
        return payload

    def _write(self, records: list[dict]) -> None:
        # Write-then-replace so a crash mid-write cannot truncate the journal.
        temporary = self.path.with_suffix(".tmp")
        temporary.write_text(json.dumps(records, indent=2), encoding="utf-8")
        os.replace(temporary, self.path)

    def record_intent(self, owned: OwnedAddress) -> None:
        """Persist the claim *before* the address exists."""
        records = self._read()
        records.append(asdict(owned))
        self._write(records)

    def update_state(self, operation_id: str, state: JournalState,
                     note: str | None = None) -> None:
        records = self._read()
        for record in records:
            if record.get("operation_id") == operation_id:
                record["state"] = state
                if note:
                    record.setdefault("notes", []).append(note)
        self._write(records)

    def clear(self, operation_id: str) -> None:
        """Remove a record only once its address is confirmed gone."""
        records = [
            record
            for record in self._read()
            if record.get("operation_id") != operation_id
        ]
        self._write(records)

    def outstanding(self) -> list[OwnedAddress]:
        """Records that did not reach COMPLETED, newest first."""
        found = [
            OwnedAddress(**record)
            for record in self._read()
            if record.get("state") != "COMPLETED"
        ]
        return sorted(found, key=lambda item: item.created_at, reverse=True)


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
