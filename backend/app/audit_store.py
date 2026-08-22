"""Audit store: SQLite + JSONL.

Stores every executed command sequence with redacted output excerpts.
"""
from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from threading import Lock
from typing import List, Optional

from .config import DATA_DIR, LOG_DIR
from .file_security import harden_private_file
from .logging_config import redact
from .models import AuditEvent


DB_PATH = DATA_DIR / "audit.sqlite"
JSONL_PATH = LOG_DIR / "command-audit.jsonl"

_DDL = """
CREATE TABLE IF NOT EXISTS audit_events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    timestamp TEXT NOT NULL,
    actor TEXT NOT NULL,
    action TEXT NOT NULL,
    commands TEXT NOT NULL,
    success INTEGER NOT NULL,
    duration_ms INTEGER NOT NULL,
    output_path TEXT,
    error_type TEXT,
    error_message TEXT,
    before_state TEXT,
    after_state TEXT
);
"""


class AuditStore:
    def __init__(self, db_path: Path = DB_PATH, jsonl_path: Path = JSONL_PATH) -> None:
        self.db_path = db_path
        self.jsonl_path = jsonl_path
        self._lock = Lock()
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self.jsonl_path.parent.mkdir(parents=True, exist_ok=True)
        with self._connect() as conn:
            conn.executescript(_DDL)
        harden_private_file(self.db_path)

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        return conn

    def record(
        self,
        *,
        actor: str,
        action: str,
        commands: List[str],
        success: bool,
        duration_ms: int,
        output_path: Optional[str] = None,
        error_type: Optional[str] = None,
        error_message: Optional[str] = None,
        before_state: Optional[str] = None,
        after_state: Optional[str] = None,
    ) -> AuditEvent:
        ts = datetime.now(timezone.utc)
        safe_commands = [redact(c) for c in commands]
        event = AuditEvent(
            timestamp=ts,
            actor=actor,
            action=action,
            commands=safe_commands,
            success=success,
            durationMs=duration_ms,
            outputPath=output_path,
            errorType=error_type,
            errorMessage=redact(error_message) if error_message else None,
            beforeState=redact(before_state) if before_state else None,
            afterState=redact(after_state) if after_state else None,
        )
        with self._lock, self._connect() as conn:
            cur = conn.execute(
                """INSERT INTO audit_events
                (timestamp, actor, action, commands, success, duration_ms,
                 output_path, error_type, error_message, before_state, after_state)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    ts.isoformat(),
                    actor,
                    action,
                    json.dumps(safe_commands),
                    1 if success else 0,
                    duration_ms,
                    output_path,
                    error_type,
                    event.error_message,
                    event.before_state,
                    event.after_state,
                ),
            )
            event.id = cur.lastrowid
            conn.commit()
        jsonl_was_missing = not self.jsonl_path.exists()
        with self._lock, self.jsonl_path.open("a", encoding="utf-8") as fh:
            fh.write(event.model_dump_json(by_alias=True) + "\n")
        if jsonl_was_missing:
            harden_private_file(self.jsonl_path)
        return event

    def recent(self, limit: int = 100) -> List[AuditEvent]:
        with self._lock, self._connect() as conn:
            rows = conn.execute(
                "SELECT * FROM audit_events ORDER BY id DESC LIMIT ?", (limit,)
            ).fetchall()
        out: List[AuditEvent] = []
        for r in rows:
            out.append(
                AuditEvent(
                    id=r["id"],
                    timestamp=datetime.fromisoformat(r["timestamp"]),
                    actor=r["actor"],
                    action=r["action"],
                    commands=json.loads(r["commands"]),
                    success=bool(r["success"]),
                    durationMs=r["duration_ms"],
                    outputPath=r["output_path"],
                    errorType=r["error_type"],
                    errorMessage=r["error_message"],
                    beforeState=r["before_state"],
                    afterState=r["after_state"],
                )
            )
        return out


_store: Optional[AuditStore] = None


def get_audit_store() -> AuditStore:
    global _store
    if _store is None:
        _store = AuditStore()
    return _store
