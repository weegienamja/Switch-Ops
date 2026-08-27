"""Sensitive local configuration-version history with redacted diffs."""
from __future__ import annotations

from datetime import datetime, timezone
import difflib
import hashlib
import json
import re
import sqlite3
from pathlib import Path
from threading import Lock
from typing import Optional

from .config import BACKUP_DIR, DATA_DIR
from .file_security import harden_private_file
from .models import ConfigurationHistoryEntry
from .parsers.config_parser import parse_running_config, redact_config


DB_PATH = DATA_DIR / "configuration-history.sqlite"
HISTORY_DIR = BACKUP_DIR / "config-history"

_DDL = """
CREATE TABLE IF NOT EXISTS configuration_versions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    timestamp TEXT NOT NULL,
    device_id TEXT NOT NULL,
    fingerprint TEXT NOT NULL,
    filename TEXT NOT NULL,
    previous_id INTEGER,
    known_good INTEGER NOT NULL DEFAULT 0,
    change_detected INTEGER NOT NULL,
    source TEXT NOT NULL,
    redacted_diff TEXT NOT NULL,
    FOREIGN KEY(previous_id) REFERENCES configuration_versions(id)
);

CREATE INDEX IF NOT EXISTS idx_configuration_versions_device_timestamp
ON configuration_versions(device_id, timestamp DESC);

CREATE INDEX IF NOT EXISTS idx_configuration_versions_known_good
ON configuration_versions(device_id, known_good)
WHERE known_good = 1;
"""


def _safe_slug(value: str) -> str:
    return re.sub(r"[^A-Za-z0-9_-]+", "-", value).strip("-") or "switch"


def _normalize_config(config_text: str) -> str:
    return config_text.replace("\r\n", "\n").replace("\r", "\n").strip() + "\n"


class ConfigurationHistoryStore:
    def __init__(
        self,
        db_path: Path = DB_PATH,
        history_dir: Path = HISTORY_DIR,
    ) -> None:
        self.db_path = db_path
        self.history_dir = history_dir
        self._lock = Lock()
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self.history_dir.mkdir(parents=True, exist_ok=True)
        with self._connect() as conn:
            conn.executescript(_DDL)
            conn.execute("PRAGMA optimize")
        harden_private_file(self.db_path)

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys = ON")
        return conn

    @staticmethod
    def _from_row(row: sqlite3.Row) -> ConfigurationHistoryEntry:
        return ConfigurationHistoryEntry(
            id=row["id"],
            timestamp=datetime.fromisoformat(row["timestamp"]),
            deviceId=row["device_id"],
            fingerprint=row["fingerprint"],
            filename=row["filename"],
            previousId=row["previous_id"],
            knownGood=bool(row["known_good"]),
            changeDetected=bool(row["change_detected"]),
            source=row["source"],
            redactedDiff=json.loads(row["redacted_diff"]),
        )

    def observe(
        self,
        *,
        device_id: str,
        hostname: str,
        config_text: str,
        observed_at: datetime | None = None,
    ) -> tuple[ConfigurationHistoryEntry, bool]:
        """Store only a new fingerprint; return the entry and whether it changed."""
        if not config_text.strip():
            raise ValueError("Cannot version an empty running configuration.")
        observed_at = observed_at or datetime.now(timezone.utc)
        if observed_at.tzinfo is None:
            observed_at = observed_at.replace(tzinfo=timezone.utc)
        normalized = _normalize_config(config_text)
        fingerprint = hashlib.sha256(normalized.encode("utf-8")).hexdigest()

        with self._lock, self._connect() as conn:
            previous = conn.execute(
                """SELECT * FROM configuration_versions
                   WHERE device_id = ? ORDER BY id DESC LIMIT 1""",
                (device_id,),
            ).fetchone()
            if previous is not None and previous["fingerprint"] == fingerprint:
                return self._from_row(previous), False

            redacted_diff: list[str] = []
            previous_id: int | None = None
            source = "initial_observation"
            if previous is not None:
                previous_id = previous["id"]
                source = "external_or_unknown"
                previous_path = self.history_dir / previous["filename"]
                previous_text = previous_path.read_text(encoding="utf-8") if previous_path.exists() else ""
                redacted_diff = list(difflib.unified_diff(
                    redact_config(_normalize_config(previous_text)).splitlines(),
                    redact_config(normalized).splitlines(),
                    fromfile=f"version-{previous_id}",
                    tofile="current-observation",
                    lineterm="",
                ))

            filename = (
                f"{_safe_slug(hostname)}-running-config-"
                f"{observed_at.strftime('%Y-%m-%d-%H%M%S-%f')}-{fingerprint[:12]}.txt"
            )
            path = self.history_dir / filename
            with path.open("x", encoding="utf-8") as handle:
                handle.write(normalized)
            harden_private_file(path)

            cursor = conn.execute(
                """INSERT INTO configuration_versions
                   (timestamp, device_id, fingerprint, filename, previous_id,
                    known_good, change_detected, source, redacted_diff)
                   VALUES (?, ?, ?, ?, ?, 0, ?, ?, ?)""",
                (
                    observed_at.isoformat(),
                    device_id,
                    fingerprint,
                    filename,
                    previous_id,
                    1 if previous is not None else 0,
                    source,
                    json.dumps(redacted_diff),
                ),
            )
            conn.commit()
            row = conn.execute(
                "SELECT * FROM configuration_versions WHERE id = ?",
                (cursor.lastrowid,),
            ).fetchone()
        assert row is not None
        return self._from_row(row), previous is not None

    def recent(self, *, device_id: str | None = None, limit: int = 50) -> list[ConfigurationHistoryEntry]:
        if device_id:
            query = "SELECT * FROM configuration_versions WHERE device_id = ? ORDER BY id DESC LIMIT ?"
            params: tuple[object, ...] = (device_id, max(1, min(limit, 200)))
        else:
            query = "SELECT * FROM configuration_versions ORDER BY id DESC LIMIT ?"
            params = (max(1, min(limit, 200)),)
        with self._lock, self._connect() as conn:
            rows = conn.execute(query, params).fetchall()
        return [self._from_row(row) for row in rows]

    def management_context_for_target(self, target: str) -> dict[str, object] | None:
        """Read only the bounded network context from a retained configuration."""
        with self._lock, self._connect() as conn:
            rows = conn.execute(
                "SELECT device_id, timestamp, filename FROM configuration_versions ORDER BY id DESC"
            ).fetchall()
        history_root = self.history_dir.resolve()
        for row in rows:
            candidate = (history_root / row["filename"]).resolve()
            if candidate.parent != history_root or not candidate.exists():
                continue
            try:
                parsed = parse_running_config(candidate.read_text(encoding="utf-8"))
                observed_at = datetime.fromisoformat(row["timestamp"])
            except (OSError, UnicodeError, ValueError):
                continue
            if str(parsed.get("management_ip") or "") != target:
                continue
            return {
                "device_id": row["device_id"],
                "observed_at": observed_at,
                "management_ip": target,
                "management_mask": parsed.get("management_mask"),
                "gateway": parsed.get("gateway"),
            }
        return None

    def mark_known_good(self, entry_id: int) -> ConfigurationHistoryEntry:
        with self._lock, self._connect() as conn:
            target = conn.execute(
                "SELECT * FROM configuration_versions WHERE id = ?",
                (entry_id,),
            ).fetchone()
            if target is None:
                raise KeyError(entry_id)
            conn.execute(
                "UPDATE configuration_versions SET known_good = 0 WHERE device_id = ?",
                (target["device_id"],),
            )
            conn.execute(
                "UPDATE configuration_versions SET known_good = 1 WHERE id = ?",
                (entry_id,),
            )
            conn.commit()
            updated = conn.execute(
                "SELECT * FROM configuration_versions WHERE id = ?",
                (entry_id,),
            ).fetchone()
        assert updated is not None
        return self._from_row(updated)


_store: Optional[ConfigurationHistoryStore] = None


def get_configuration_history_store() -> ConfigurationHistoryStore:
    global _store
    if _store is None:
        _store = ConfigurationHistoryStore()
    return _store
