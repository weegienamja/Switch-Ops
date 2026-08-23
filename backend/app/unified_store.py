"""Durable local storage for normalized Unified Lab state and decisions."""
from __future__ import annotations

from datetime import datetime, timezone
import json
from pathlib import Path
import sqlite3
import threading

from .config import DATA_DIR
from .file_security import harden_private_file
from .unified_models import NormalizedClaim, ProviderEntity, SourceHealth


STORE_PATH = DATA_DIR / "unified-lab.sqlite"
SCHEMA_VERSION = 1


class UnifiedLabStore:
    def __init__(self, path: Path = STORE_PATH) -> None:
        self._path = path
        self._path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()
        self._connection = sqlite3.connect(path, check_same_thread=False)
        self._connection.row_factory = sqlite3.Row
        self._initialize()
        harden_private_file(path)

    def _initialize(self) -> None:
        with self._connection:
            self._connection.executescript(
                """
                CREATE TABLE IF NOT EXISTS unified_meta (
                    key TEXT PRIMARY KEY,
                    value TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS provider_snapshots (
                    provider TEXT PRIMARY KEY,
                    observed_at TEXT NOT NULL,
                    entities_json TEXT NOT NULL,
                    claims_json TEXT NOT NULL,
                    health_json TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS identity_decisions (
                    link_id TEXT PRIMARY KEY,
                    decision TEXT NOT NULL CHECK (decision IN ('confirm', 'reject')),
                    decided_at TEXT NOT NULL
                );
                """
            )
            self._connection.execute(
                "INSERT OR REPLACE INTO unified_meta(key, value) VALUES('schema_version', ?)",
                (str(SCHEMA_VERSION),),
            )

    def close(self) -> None:
        with self._lock:
            self._connection.close()

    def save_provider_state(
        self,
        provider: str,
        entities: list[ProviderEntity],
        claims: list[NormalizedClaim],
        health: SourceHealth,
        *,
        observed_at: datetime | None = None,
    ) -> None:
        observed_at = observed_at or health.checked_at
        entities_json = json.dumps(
            [item.model_dump(by_alias=True, mode="json") for item in entities],
            separators=(",", ":"),
        )
        claims_json = json.dumps(
            [item.model_dump(by_alias=True, mode="json") for item in claims],
            separators=(",", ":"),
        )
        health_json = health.model_dump_json(by_alias=True)
        with self._lock, self._connection:
            self._connection.execute(
                """
                INSERT INTO provider_snapshots(provider, observed_at, entities_json, claims_json, health_json)
                VALUES(?, ?, ?, ?, ?)
                ON CONFLICT(provider) DO UPDATE SET
                    observed_at=excluded.observed_at,
                    entities_json=excluded.entities_json,
                    claims_json=excluded.claims_json,
                    health_json=excluded.health_json
                """,
                (provider, observed_at.isoformat(), entities_json, claims_json, health_json),
            )

    def save_source_health(self, health: SourceHealth) -> None:
        """Update source health without deleting the last successful evidence."""
        with self._lock:
            row = self._connection.execute(
                "SELECT entities_json, claims_json FROM provider_snapshots WHERE provider = ?",
                (health.provider,),
            ).fetchone()
        entities = [ProviderEntity.model_validate(item) for item in json.loads(row[0])] if row else []
        claims = [NormalizedClaim.model_validate(item) for item in json.loads(row[1])] if row else []
        self.save_provider_state(health.provider, entities, claims, health)

    def load_provider_states(
        self,
    ) -> tuple[list[ProviderEntity], list[NormalizedClaim], list[SourceHealth]]:
        with self._lock:
            rows = self._connection.execute(
                "SELECT entities_json, claims_json, health_json FROM provider_snapshots ORDER BY provider"
            ).fetchall()
        entities: list[ProviderEntity] = []
        claims: list[NormalizedClaim] = []
        health: list[SourceHealth] = []
        for row in rows:
            entities.extend(ProviderEntity.model_validate(item) for item in json.loads(row[0]))
            claims.extend(NormalizedClaim.model_validate(item) for item in json.loads(row[1]))
            health.append(SourceHealth.model_validate_json(row[2]))
        return entities, claims, health

    def set_identity_decision(
        self,
        link_id: str,
        decision: str,
        *,
        decided_at: datetime | None = None,
    ) -> None:
        if decision not in {"confirm", "reject"}:
            raise ValueError("Identity decision is invalid.")
        decided_at = decided_at or datetime.now(timezone.utc)
        with self._lock, self._connection:
            self._connection.execute(
                """
                INSERT INTO identity_decisions(link_id, decision, decided_at)
                VALUES(?, ?, ?)
                ON CONFLICT(link_id) DO UPDATE SET
                    decision=excluded.decision,
                    decided_at=excluded.decided_at
                """,
                (link_id, decision, decided_at.isoformat()),
            )

    def clear_identity_decision(self, link_id: str) -> None:
        with self._lock, self._connection:
            self._connection.execute(
                "DELETE FROM identity_decisions WHERE link_id = ?",
                (link_id,),
            )

    def load_identity_decisions(self) -> dict[str, tuple[str, datetime]]:
        with self._lock:
            rows = self._connection.execute(
                "SELECT link_id, decision, decided_at FROM identity_decisions"
            ).fetchall()
        return {
            str(row[0]): (str(row[1]), datetime.fromisoformat(str(row[2])))
            for row in rows
        }


_store: UnifiedLabStore | None = None


def get_unified_lab_store() -> UnifiedLabStore:
    global _store
    if _store is None:
        _store = UnifiedLabStore()
    return _store
