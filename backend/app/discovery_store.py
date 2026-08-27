"""SQLite continuity for discovered entities and evidence.

This store is local history, not another topology engine. The in-memory
evidence builder remains authoritative; persistence supplies first-seen times,
survives a failed poll, and retains revoked observations as historical facts.
"""
from __future__ import annotations

from datetime import datetime, timezone
import json
from pathlib import Path
import sqlite3
from threading import Lock
from typing import Optional

from .config import DATA_DIR
from .discovery_evidence import evidence_record, freshness_for
from .file_security import harden_private_file
from .models import (
    DiscoveryEvidence,
    EvidenceClaimSupport,
    NetworkDevice,
    NetworkLink,
    TopologyModel,
    TopologyTransition,
)


DB_PATH = DATA_DIR / "discovery-history.sqlite"
SCHEMA_VERSION = 1

_SCHEMA_V1 = """
CREATE TABLE IF NOT EXISTS discovered_entities (
    device_id TEXT NOT NULL,
    entity_id TEXT NOT NULL,
    first_seen TEXT NOT NULL,
    last_seen TEXT NOT NULL,
    last_interface TEXT,
    active INTEGER NOT NULL DEFAULT 1,
    entity_json TEXT NOT NULL,
    link_json TEXT,
    PRIMARY KEY (device_id, entity_id)
);

CREATE TABLE IF NOT EXISTS discovery_evidence (
    device_id TEXT NOT NULL,
    evidence_id TEXT NOT NULL,
    entity_id TEXT,
    interface TEXT,
    evidence_type TEXT NOT NULL,
    first_seen TEXT NOT NULL,
    last_seen TEXT NOT NULL,
    active INTEGER NOT NULL DEFAULT 1,
    evidence_json TEXT NOT NULL,
    PRIMARY KEY (device_id, evidence_id)
);

CREATE TABLE IF NOT EXISTS discovery_evidence_observations (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    device_id TEXT NOT NULL,
    evidence_id TEXT NOT NULL,
    observed_at TEXT NOT NULL,
    freshness TEXT NOT NULL,
    revoked INTEGER NOT NULL DEFAULT 0,
    UNIQUE (device_id, evidence_id, observed_at)
);

CREATE INDEX IF NOT EXISTS idx_discovered_entities_port
ON discovered_entities(device_id, last_interface, last_seen DESC);

CREATE INDEX IF NOT EXISTS idx_discovery_evidence_entity
ON discovery_evidence(device_id, entity_id, last_seen DESC);

CREATE INDEX IF NOT EXISTS idx_discovery_observations_time
ON discovery_evidence_observations(device_id, observed_at DESC);
"""


class DiscoveryHistoryStore:
    def __init__(self, db_path: Path = DB_PATH) -> None:
        self.db_path = db_path
        self._lock = Lock()
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        with self._connect() as conn:
            self._migrate(conn)
        harden_private_file(self.db_path)

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        return conn

    def latest_local_host(self, device_id: str) -> dict[str, object] | None:
        """Return minimal historical host attachment evidence for one device."""
        with self._lock, self._connect() as conn:
            rows = conn.execute(
                """SELECT last_seen, last_interface, entity_json
                   FROM discovered_entities
                   WHERE device_id = ? ORDER BY last_seen DESC""",
                (device_id,),
            ).fetchall()
        for row in rows:
            try:
                payload = json.loads(row["entity_json"])
                last_seen = datetime.fromisoformat(row["last_seen"])
            except (TypeError, json.JSONDecodeError, ValueError):
                continue
            if payload.get("identitySource") != "local-host":
                continue
            return {
                "last_seen": last_seen,
                "interface": row["last_interface"],
                "ip": payload.get("ip"),
                "mac": payload.get("mac"),
            }
        return None

    @staticmethod
    def _migrate(conn: sqlite3.Connection) -> None:
        version = int(conn.execute("PRAGMA user_version").fetchone()[0])
        if version > SCHEMA_VERSION:
            raise RuntimeError(
                f"Discovery database schema {version} is newer than supported {SCHEMA_VERSION}."
            )
        if version < 1:
            conn.executescript(_SCHEMA_V1)
            conn.execute(f"PRAGMA user_version = {SCHEMA_VERSION}")

    @staticmethod
    def schema_version(db_path: Path) -> int:
        with sqlite3.connect(db_path) as conn:
            return int(conn.execute("PRAGMA user_version").fetchone()[0])

    @staticmethod
    def _device_from_row(row: sqlite3.Row) -> NetworkDevice:
        return NetworkDevice.model_validate_json(row["entity_json"])

    @staticmethod
    def _link_from_row(row: sqlite3.Row) -> NetworkLink | None:
        payload = row["link_json"]
        return NetworkLink.model_validate_json(payload) if payload else None

    def apply_observation(
        self,
        topology: TopologyModel,
        *,
        complete: bool,
        observed_at: datetime,
        connection_state: str = "live",
    ) -> TopologyModel:
        """Persist and enrich one topology observation.

        A complete successful observation revokes facts that disappeared. An
        incomplete observation never fabricates negative evidence: previously
        active entities are returned as AGING/STALE until a complete poll can
        confirm or revoke them.
        """
        device_id = topology.root_device_id
        root = next((item for item in topology.devices if item.id == device_id), None)
        current = [item for item in topology.devices if item.id != device_id]
        current_ids = {item.id for item in current}
        link_by_entity = {item.to_device_id: item for item in topology.links}
        evidence_ids = {item.id for item in topology.evidence}
        stamp = observed_at.isoformat()

        with self._lock, self._connect() as conn:
            previous_rows = conn.execute(
                "SELECT * FROM discovered_entities WHERE device_id = ?",
                (device_id,),
            ).fetchall()
            previous_by_id = {row["entity_id"]: row for row in previous_rows}
            previous_active = {row["entity_id"]: row for row in previous_rows if row["active"]}
            previous_active_evidence = {
                row["evidence_id"]: row
                for row in conn.execute(
                    "SELECT * FROM discovery_evidence WHERE device_id = ? AND active = 1",
                    (device_id,),
                ).fetchall()
            }

            if complete:
                conn.execute(
                    "UPDATE discovered_entities SET active = 0 WHERE device_id = ?",
                    (device_id,),
                )
                conn.execute(
                    "UPDATE discovery_evidence SET active = 0 WHERE device_id = ?",
                    (device_id,),
                )

            for entity in current:
                old = previous_by_id.get(entity.id)
                first_seen = old["first_seen"] if old else stamp
                entity.first_seen = datetime.fromisoformat(first_seen)
                entity.last_seen = observed_at

                if old is not None:
                    prior_entity = self._device_from_row(old)
                    prior_interface = old["last_interface"]
                    if (
                        prior_interface
                        and entity.connected_interface
                        and prior_interface != entity.connected_interface
                    ):
                        entity.previous_connected_interface = prior_interface
                        entity.attachment_state = "moved"
                        entity.attachment_confidence = (
                            "high"
                            if entity.identity_confidence in {"high", "confirmed"}
                            or entity.identity_source in {"local-host", "cdp", "lldp"}
                            else "medium"
                        )
                        topology.transitions.append(
                            TopologyTransition(
                                kind="ENDPOINT_MOVED",
                                entityId=entity.id,
                                previousInterface=prior_interface,
                                currentInterface=entity.connected_interface,
                                locations=[prior_interface, entity.connected_interface],
                                identityRetained=True,
                                identityConfidence=entity.identity_confidence,
                                attachmentConfidence=entity.attachment_confidence,
                                observedAt=observed_at,
                                detail=(
                                    "The same stable endpoint identity was observed on a "
                                    "different current interface."
                                ),
                            )
                        )
                    else:
                        entity.previous_connected_interface = (
                            prior_entity.previous_connected_interface
                        )
                        if entity.attachment_state == "unknown":
                            entity.attachment_state = "current"
                        if entity.attachment_confidence == "unknown":
                            entity.attachment_confidence = (
                                prior_entity.attachment_confidence
                            )

                # If a new stable identity appears on a port, retain the last
                # identified occupant as historical context instead of merging
                # the two identities.
                if (
                    old is None
                    and entity.connected_interface
                    and entity.attachment_state != "ambiguous"
                ):
                    occupant = conn.execute(
                        """SELECT entity_json FROM discovered_entities
                           WHERE device_id = ? AND last_interface = ? AND entity_id != ?
                           ORDER BY last_seen DESC LIMIT 1""",
                        (device_id, entity.connected_interface, entity.id),
                    ).fetchone()
                    if occupant:
                        prior = NetworkDevice.model_validate_json(occupant["entity_json"])
                        if prior.identity_source != "none":
                            entity.historical_identity = prior.name
                            entity.previous_connected_interface = entity.connected_interface
                            topology.transitions.append(
                                TopologyTransition(
                                    kind="DEVICE_REPLACED",
                                    entityId=entity.id,
                                    previousEntityId=prior.id,
                                    previousInterface=entity.connected_interface,
                                    currentInterface=entity.connected_interface,
                                    locations=[entity.connected_interface],
                                    identityRetained=False,
                                    identityConfidence=entity.identity_confidence,
                                    attachmentConfidence=entity.attachment_confidence,
                                    observedAt=observed_at,
                                    detail=(
                                        "A different stable endpoint identity replaced the "
                                        "previous identified occupant on this interface."
                                    ),
                                )
                            )

                link = link_by_entity.get(entity.id)
                conn.execute(
                    """INSERT INTO discovered_entities
                           (device_id, entity_id, first_seen, last_seen,
                            last_interface, active, entity_json, link_json)
                       VALUES (?, ?, ?, ?, ?, 1, ?, ?)
                       ON CONFLICT(device_id, entity_id) DO UPDATE SET
                           last_seen = excluded.last_seen,
                           last_interface = excluded.last_interface,
                           active = 1,
                           entity_json = excluded.entity_json,
                           link_json = excluded.link_json""",
                    (
                        device_id,
                        entity.id,
                        first_seen,
                        stamp,
                        entity.connected_interface,
                        entity.model_dump_json(by_alias=True),
                        link.model_dump_json(by_alias=True) if link else None,
                    ),
                )

            for record in topology.evidence:
                prior = conn.execute(
                    "SELECT first_seen FROM discovery_evidence WHERE device_id = ? AND evidence_id = ?",
                    (device_id, record.id),
                ).fetchone()
                first_seen = prior["first_seen"] if prior else stamp
                conn.execute(
                    """INSERT INTO discovery_evidence
                           (device_id, evidence_id, entity_id, interface,
                            evidence_type, first_seen, last_seen, active, evidence_json)
                       VALUES (?, ?, ?, ?, ?, ?, ?, 1, ?)
                       ON CONFLICT(device_id, evidence_id) DO UPDATE SET
                           entity_id = excluded.entity_id,
                           interface = excluded.interface,
                           evidence_type = excluded.evidence_type,
                           last_seen = excluded.last_seen,
                           active = 1,
                           evidence_json = excluded.evidence_json""",
                    (
                        device_id,
                        record.id,
                        record.entity_id,
                        record.interface,
                        record.evidence_type,
                        first_seen,
                        stamp,
                        record.model_dump_json(by_alias=True),
                    ),
                )
                conn.execute(
                    """INSERT OR IGNORE INTO discovery_evidence_observations
                           (device_id, evidence_id, observed_at, freshness, revoked)
                       VALUES (?, ?, ?, ?, ?)""",
                    (device_id, record.id, stamp, record.freshness, int(record.revoked)),
                )

            if complete:
                revoked_ids = set(previous_active) - current_ids
                for entity_id in revoked_ids:
                    row = previous_active[entity_id]
                    previous = self._device_from_row(row)
                    previous.freshness = "historical"
                    previous.existence_state = "historical"
                    previous.online = False
                    conn.execute(
                        "UPDATE discovered_entities SET entity_json = ? WHERE device_id = ? AND entity_id = ?",
                        (previous.model_dump_json(by_alias=True), device_id, entity_id),
                    )

                revoked_evidence_ids = set(previous_active_evidence) - evidence_ids
                revoked_evidence = [
                    previous_active_evidence[evidence_id]
                    for evidence_id in revoked_evidence_ids
                ]
                for row in revoked_evidence:
                    record = DiscoveryEvidence.model_validate_json(row["evidence_json"])
                    record.revoked = True
                    record.freshness = "historical"
                    conn.execute(
                        "UPDATE discovery_evidence SET evidence_json = ? WHERE device_id = ? AND evidence_id = ?",
                        (record.model_dump_json(by_alias=True), device_id, record.id),
                    )
                    conn.execute(
                        """INSERT OR IGNORE INTO discovery_evidence_observations
                               (device_id, evidence_id, observed_at, freshness, revoked)
                           VALUES (?, ?, ?, 'historical', 1)""",
                        (device_id, record.id, stamp),
                    )
            else:
                # Keep last known entities visible but visibly age them. This
                # is continuity during a failed command/session, not a claim
                # that they were re-observed.
                missing_rows = [row for key, row in previous_active.items() if key not in current_ids]
                for row in missing_rows:
                    prior = self._device_from_row(row)
                    prior.freshness = freshness_for(
                        evidence_type="INTERFACE_LINK",
                        observed_at=datetime.fromisoformat(row["last_seen"]),
                        reference_at=observed_at,
                        connection_state=connection_state,
                    )
                    prior.online = prior.freshness == "current" and prior.online
                    current.append(prior)
                    prior_link = self._link_from_row(row)
                    if prior_link:
                        prior_link.freshness = prior.freshness
                        prior_link.status = "unknown"
                        topology.links.append(prior_link)

                cached_rows = conn.execute(
                    "SELECT evidence_json, last_seen FROM discovery_evidence WHERE device_id = ? AND active = 1",
                    (device_id,),
                ).fetchall()
                for row in cached_rows:
                    record = DiscoveryEvidence.model_validate_json(row["evidence_json"])
                    if record.id in evidence_ids:
                        continue
                    record.freshness = freshness_for(
                        evidence_type=record.evidence_type,
                        observed_at=datetime.fromisoformat(row["last_seen"]),
                        reference_at=observed_at,
                        connection_state=connection_state,
                    )
                    topology.evidence.append(record)

            historical_rows = conn.execute(
                """SELECT * FROM discovered_entities
                   WHERE device_id = ? AND active = 0
                   ORDER BY last_seen DESC LIMIT 50""",
                (device_id,),
            ).fetchall()

        historical: list[NetworkDevice] = []
        for row in historical_rows:
            item = self._device_from_row(row)
            item.freshness = "historical"
            item.existence_state = "historical"
            item.online = False
            prior = evidence_record(
                evidence_type="PRIOR_OBSERVATION",
                evidence_class="historical",
                source="prior-observation",
                device_id=device_id,
                interface=item.connected_interface,
                entity_id=item.id,
                observed_value=item.name,
                summary=(
                    f"{item.name} was last observed on {item.connected_interface or 'an unknown interface'} "
                    f"at {row['last_seen']}. It is not current evidence."
                ),
                observed_at=datetime.fromisoformat(row["last_seen"]),
                strength="medium" if item.identity_source != "none" else "low",
                establishes=EvidenceClaimSupport(
                    existence=True,
                    identity=item.identity_source != "none",
                    relationship=item.relationship is not None,
                ),
                relationship=item.relationship,
                provenance="SwitchOps local discovery history",
            )
            prior.freshness = "historical"
            prior.revoked = True
            topology.evidence.append(prior)
            item.evidence_ids = [prior.id]
            historical.append(item)

        topology.devices = ([root] if root else []) + current
        topology.historical_devices = historical
        return topology

    def observation_count(self, device_id: str) -> int:
        with self._lock, self._connect() as conn:
            row = conn.execute(
                "SELECT COUNT(*) AS count FROM discovery_evidence_observations WHERE device_id = ?",
                (device_id,),
            ).fetchone()
        return int(row["count"])


_store: Optional[DiscoveryHistoryStore] = None


def get_discovery_store() -> DiscoveryHistoryStore:
    global _store
    if _store is None:
        _store = DiscoveryHistoryStore()
    return _store


def reset_discovery_store() -> None:
    global _store
    _store = None
