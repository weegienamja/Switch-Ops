"""SwitchOps-local expected topology, and the reconciliation state that
decides when a discrepancy is worth telling the user about.

Two things live here because they are two halves of the same idea:

``expected_relationships``
    What the operator says should be on an interface. This is SwitchOps' own
    record. Writing one never touches the switch - the interface description
    on the device is left exactly as it was, and the resulting disagreement
    between intent and device documentation is itself reported.

``reconciliation_state``
    The last reconciliation signature seen for each interface. A discrepancy
    that persists across twenty refreshes is one situation, not twenty events,
    so an event is only raised when the signature changes.

Neither table stores hardware addresses. Reconciliation works from interface
identity and labels, so there is nothing here to leak.
"""
from __future__ import annotations

import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from threading import Lock
from typing import Optional

from .config import DATA_DIR
from .file_security import harden_private_file
from .models import ExpectedRelationship, IntentSource


DB_PATH = DATA_DIR / "topology-intent.sqlite"

_DDL = """
CREATE TABLE IF NOT EXISTS expected_relationships (
    device_id TEXT NOT NULL,
    interface TEXT NOT NULL,
    expected_name TEXT NOT NULL,
    expected_device_type TEXT NOT NULL DEFAULT 'unknown',
    expected_vendor TEXT,
    expected_model TEXT,
    source TEXT NOT NULL,
    note TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    PRIMARY KEY (device_id, interface)
);

CREATE TABLE IF NOT EXISTS reconciliation_state (
    device_id TEXT NOT NULL,
    interface TEXT NOT NULL,
    signature TEXT NOT NULL,
    status TEXT NOT NULL,
    -- The identity observed last time, so the next observation can tell a
    -- neighbour swap from a neighbour that was never identified. Labels only;
    -- no hardware address is stored.
    observed_label TEXT,
    observed_identified INTEGER NOT NULL DEFAULT 0,
    first_seen TEXT NOT NULL,
    last_seen TEXT NOT NULL,
    PRIMARY KEY (device_id, interface)
);
"""


class TopologyIntentStore:
    def __init__(self, db_path: Path = DB_PATH) -> None:
        self.db_path = db_path
        self._lock = Lock()
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        with self._connect() as conn:
            conn.executescript(_DDL)
        harden_private_file(self.db_path)

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        return conn

    # --- expected topology ------------------------------------------------

    @staticmethod
    def _from_row(row: sqlite3.Row) -> ExpectedRelationship:
        return ExpectedRelationship(
            deviceId=row["device_id"],
            interface=row["interface"],
            expectedName=row["expected_name"],
            expectedDeviceType=row["expected_device_type"],
            expectedVendor=row["expected_vendor"],
            expectedModel=row["expected_model"],
            source=row["source"],
            note=row["note"],
            createdAt=datetime.fromisoformat(row["created_at"]),
            updatedAt=datetime.fromisoformat(row["updated_at"]),
        )

    def list_expected(self, device_id: str) -> list[ExpectedRelationship]:
        with self._lock, self._connect() as conn:
            rows = conn.execute(
                "SELECT * FROM expected_relationships WHERE device_id = ? ORDER BY interface",
                (device_id,),
            ).fetchall()
        return [self._from_row(row) for row in rows]

    def set_expected(
        self,
        *,
        device_id: str,
        interface: str,
        expected_name: str,
        expected_device_type: str = "unknown",
        expected_vendor: Optional[str] = None,
        expected_model: Optional[str] = None,
        source: IntentSource = "user-intent",
        note: Optional[str] = None,
        now: Optional[datetime] = None,
    ) -> ExpectedRelationship:
        """Record what should be on an interface. Local only; no IOS is sent."""
        now = now or datetime.now(timezone.utc)
        stamp = now.isoformat()
        with self._lock, self._connect() as conn:
            existing = conn.execute(
                "SELECT created_at FROM expected_relationships WHERE device_id = ? AND interface = ?",
                (device_id, interface),
            ).fetchone()
            created = existing["created_at"] if existing else stamp
            conn.execute(
                """
                INSERT INTO expected_relationships
                    (device_id, interface, expected_name, expected_device_type,
                     expected_vendor, expected_model, source, note, created_at, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(device_id, interface) DO UPDATE SET
                    expected_name = excluded.expected_name,
                    expected_device_type = excluded.expected_device_type,
                    expected_vendor = excluded.expected_vendor,
                    expected_model = excluded.expected_model,
                    source = excluded.source,
                    note = excluded.note,
                    updated_at = excluded.updated_at
                """,
                (
                    device_id, interface, expected_name, expected_device_type,
                    expected_vendor, expected_model, source, note, created, stamp,
                ),
            )
            row = conn.execute(
                "SELECT * FROM expected_relationships WHERE device_id = ? AND interface = ?",
                (device_id, interface),
            ).fetchone()
        return self._from_row(row)

    def clear_expected(self, *, device_id: str, interface: str) -> bool:
        """Forget a recorded expectation. Intent falls back to the description."""
        with self._lock, self._connect() as conn:
            cursor = conn.execute(
                "DELETE FROM expected_relationships WHERE device_id = ? AND interface = ?",
                (device_id, interface),
            )
            return cursor.rowcount > 0

    # --- reconciliation state (event de-duplication) ----------------------

    def observe_reconciliation(
        self,
        *,
        device_id: str,
        interface: str,
        signature: str,
        status: str,
        observed_label: Optional[str] = None,
        observed_identified: bool = False,
        now: Optional[datetime] = None,
    ) -> tuple[bool, Optional[str], Optional[datetime]]:
        """Record the current reconciliation signature for an interface.

        Returns ``(changed, previous_signature, first_seen)``. ``changed`` is
        False when the same situation is simply still true, which is what keeps
        a persistent discrepancy from producing an event on every refresh.
        """
        now = now or datetime.now(timezone.utc)
        stamp = now.isoformat()
        with self._lock, self._connect() as conn:
            row = conn.execute(
                "SELECT signature, first_seen FROM reconciliation_state WHERE device_id = ? AND interface = ?",
                (device_id, interface),
            ).fetchone()
            previous_signature = row["signature"] if row else None
            first_seen = (
                datetime.fromisoformat(row["first_seen"]) if row else None
            )
            changed = previous_signature != signature
            conn.execute(
                """
                INSERT INTO reconciliation_state
                    (device_id, interface, signature, status, observed_label,
                     observed_identified, first_seen, last_seen)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(device_id, interface) DO UPDATE SET
                    signature = excluded.signature,
                    status = excluded.status,
                    observed_label = excluded.observed_label,
                    observed_identified = excluded.observed_identified,
                    first_seen = CASE
                        WHEN reconciliation_state.signature = excluded.signature
                        THEN reconciliation_state.first_seen
                        ELSE excluded.first_seen
                    END,
                    last_seen = excluded.last_seen
                """,
                (
                    device_id, interface, signature, status,
                    observed_label, int(bool(observed_identified)), stamp, stamp,
                ),
            )
        return changed, previous_signature, first_seen

    def previous_observations(self, device_id: str) -> dict[str, tuple[Optional[str], bool]]:
        """Per-interface ``(observed_label, identified)`` from the last run."""
        with self._lock, self._connect() as conn:
            rows = conn.execute(
                "SELECT interface, observed_label, observed_identified "
                "FROM reconciliation_state WHERE device_id = ?",
                (device_id,),
            ).fetchall()
        return {
            row["interface"]: (row["observed_label"], bool(row["observed_identified"]))
            for row in rows
        }

    def forget_reconciliation(self, *, device_id: str) -> None:
        with self._lock, self._connect() as conn:
            conn.execute("DELETE FROM reconciliation_state WHERE device_id = ?", (device_id,))


_store: Optional[TopologyIntentStore] = None


def get_intent_store() -> TopologyIntentStore:
    global _store
    if _store is None:
        _store = TopologyIntentStore()
    return _store


def reset_intent_store() -> None:
    """Test hook: drop the cached singleton."""
    global _store
    _store = None
