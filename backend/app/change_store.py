"""Durable local persistence for Change Assurance sessions."""
from __future__ import annotations

import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from threading import RLock
from typing import Optional

from .change_models import ChangeSession
from .config import DATA_DIR
from .file_security import harden_private_file


DB_PATH = DATA_DIR / "change-sessions.sqlite"
SCHEMA_VERSION = 1
_IN_FLIGHT = ("executing", "verifying", "rolling_back")

_DDL = """
CREATE TABLE IF NOT EXISTS schema_metadata (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS change_sessions (
    id TEXT PRIMARY KEY,
    device_id TEXT NOT NULL,
    target_interface TEXT NOT NULL,
    operation_kind TEXT NOT NULL,
    status TEXT NOT NULL,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    document TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_change_sessions_updated
ON change_sessions(updated_at DESC);

CREATE INDEX IF NOT EXISTS idx_change_sessions_device_updated
ON change_sessions(device_id, updated_at DESC);
"""


class ChangeStore:
    def __init__(self, db_path: Path = DB_PATH) -> None:
        self.db_path = db_path
        self._lock = RLock()
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        with self._connect() as conn:
            conn.executescript(_DDL)
            version = conn.execute(
                "SELECT value FROM schema_metadata WHERE key = 'schema_version'"
            ).fetchone()
            if version is None:
                conn.execute(
                    "INSERT INTO schema_metadata(key, value) VALUES ('schema_version', ?)",
                    (str(SCHEMA_VERSION),),
                )
            elif int(version["value"]) != SCHEMA_VERSION:
                raise ValueError("Unsupported Change Assurance database schema.")
            conn.commit()
        harden_private_file(self.db_path)
        self._recover_incomplete()

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        return conn

    @staticmethod
    def _decode(row: sqlite3.Row) -> ChangeSession:
        return ChangeSession.model_validate_json(row["document"])

    def _recover_incomplete(self) -> None:
        """Never present an interrupted write as still running or successful."""
        with self._lock, self._connect() as conn:
            rows = conn.execute(
                "SELECT * FROM change_sessions WHERE status IN (?, ?, ?)", _IN_FLIGHT
            ).fetchall()
            for row in rows:
                session = self._decode(row)
                session.status = "indeterminate"
                session.outcome_detail = (
                    "SwitchOps stopped while this change was in progress. The device state "
                    "must be inspected before another change is attempted."
                )
                session.updated_at = datetime.now(timezone.utc)
                self._write(conn, session)
            conn.commit()

    @staticmethod
    def _write(conn: sqlite3.Connection, session: ChangeSession) -> None:
        step = session.plan.steps[0]
        conn.execute(
            """INSERT INTO change_sessions
               (id, device_id, target_interface, operation_kind, status,
                created_at, updated_at, document)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?)
               ON CONFLICT(id) DO UPDATE SET
                 device_id = excluded.device_id,
                 target_interface = excluded.target_interface,
                 operation_kind = excluded.operation_kind,
                 status = excluded.status,
                 updated_at = excluded.updated_at,
                 document = excluded.document""",
            (
                session.id,
                session.plan.device_id,
                session.plan.target_interface,
                step.kind,
                session.status,
                session.created_at.isoformat(),
                session.updated_at.isoformat(),
                session.model_dump_json(by_alias=True),
            ),
        )

    def save(self, session: ChangeSession) -> ChangeSession:
        session.updated_at = datetime.now(timezone.utc)
        with self._lock, self._connect() as conn:
            self._write(conn, session)
            conn.commit()
        return session

    def get(self, session_id: str) -> Optional[ChangeSession]:
        with self._lock, self._connect() as conn:
            row = conn.execute(
                "SELECT * FROM change_sessions WHERE id = ?", (session_id,)
            ).fetchone()
        return self._decode(row) if row is not None else None

    def recent(
        self,
        *,
        limit: int = 50,
        device_id: Optional[str] = None,
    ) -> list[ChangeSession]:
        bounded = max(1, min(limit, 200))
        with self._lock, self._connect() as conn:
            if device_id:
                rows = conn.execute(
                    """SELECT * FROM change_sessions
                       WHERE device_id = ? ORDER BY updated_at DESC LIMIT ?""",
                    (device_id, bounded),
                ).fetchall()
            else:
                rows = conn.execute(
                    "SELECT * FROM change_sessions ORDER BY updated_at DESC LIMIT ?",
                    (bounded,),
                ).fetchall()
        return [self._decode(row) for row in rows]


_store: Optional[ChangeStore] = None


def get_change_store() -> ChangeStore:
    global _store
    if _store is None:
        _store = ChangeStore()
    return _store
