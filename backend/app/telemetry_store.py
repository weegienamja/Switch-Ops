"""Local SQLite telemetry and meaningful network-event history.

Collection cadence is owned by the live tier scheduler. This module never starts a timer,
opens an SSH connection, or runs a command; it only persists one already
aggregated dashboard observation and compares it with the preceding sample.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
import json
import sqlite3
from pathlib import Path
from threading import Lock
from typing import Iterable, Optional
from uuid import uuid4

from .config import DATA_DIR
from .file_security import harden_private_file
from .models import (
    CpuStatus,
    DeviceObservationPoint,
    EnvironmentStatus,
    InterfaceDelta,
    InterfaceErrorCounters,
    InterfaceStatus,
    MacTableEntry,
    MemoryStatus,
    NetworkEvent,
    PoePort,
    PoeResponse,
    TelemetryHistoryResponse,
    TelemetrySnapshotSummary,
)
from .topology import interface_admin_state


DB_PATH = DATA_DIR / "telemetry.sqlite"
COUNTER_MAX_32 = (2**32) - 1

_DDL = """
PRAGMA foreign_keys = ON;

CREATE TABLE IF NOT EXISTS device_observations (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    snapshot_id TEXT NOT NULL UNIQUE,
    device_id TEXT NOT NULL,
    timestamp TEXT NOT NULL,
    reachable INTEGER NOT NULL,
    cpu_5sec REAL,
    memory_used_pct REAL,
    temperature_c INTEGER,
    poe_used_w REAL,
    poe_available_w REAL
);

CREATE TABLE IF NOT EXISTS interface_observations (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    snapshot_id TEXT NOT NULL,
    device_id TEXT NOT NULL,
    port TEXT NOT NULL,
    status TEXT NOT NULL,
    admin_state TEXT NOT NULL,
    speed TEXT NOT NULL,
    duplex TEXT NOT NULL,
    vlan TEXT NOT NULL,
    poe_state TEXT NOT NULL,
    total_errors INTEGER NOT NULL,
    learned_devices TEXT NOT NULL,
    FOREIGN KEY(snapshot_id) REFERENCES device_observations(snapshot_id) ON DELETE CASCADE,
    UNIQUE(snapshot_id, port)
);

CREATE TABLE IF NOT EXISTS network_events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    timestamp TEXT NOT NULL,
    device_id TEXT NOT NULL,
    interface TEXT,
    event_type TEXT NOT NULL,
    severity TEXT NOT NULL,
    title TEXT NOT NULL,
    detail TEXT NOT NULL,
    metadata TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_device_observations_device_timestamp
ON device_observations(device_id, timestamp DESC);

CREATE INDEX IF NOT EXISTS idx_interface_observations_snapshot
ON interface_observations(snapshot_id);

CREATE INDEX IF NOT EXISTS idx_network_events_timestamp
ON network_events(timestamp DESC);

CREATE INDEX IF NOT EXISTS idx_network_events_device_timestamp
ON network_events(device_id, timestamp DESC);

CREATE INDEX IF NOT EXISTS idx_network_events_interface_timestamp
ON network_events(interface, timestamp DESC);
"""


def calculate_counter_delta(
    previous: int | None,
    current: int,
    *,
    counter_max: int = COUNTER_MAX_32,
) -> tuple[int | None, str]:
    """Return a delta and an explicit cumulative-counter transition state."""
    if previous is None:
        return None, "first"
    if current == previous:
        return 0, "unchanged"
    if current > previous:
        return current - previous, "increased"
    # Treat a drop near the 32-bit boundary as wraparound. Other drops are
    # resets (device restart or an externally cleared counter), not negative
    # errors and not an error spike.
    if previous >= int(counter_max * 0.9) and current <= int(counter_max * 0.1):
        return (counter_max - previous) + 1 + current, "wrapped"
    return None, "reset"


def _memory_used_pct(memory: MemoryStatus) -> float | None:
    total = memory.processor_total
    used = memory.processor_used
    if total is None or used is None or total <= 0:
        return None
    return round((used / total) * 100, 2)


def _normalize_speed(value: str) -> str:
    return value.strip().lower().removeprefix("a-")


def _normalize_duplex(value: str) -> str:
    return value.strip().lower().removeprefix("a-")


def _poe_active(value: str) -> bool:
    return value.strip().lower() not in {"", "off", "n/a", "not-supported", "unknown"}


class TelemetryStore:
    def __init__(self, db_path: Path = DB_PATH, *, retention_days: int = 30) -> None:
        self.db_path = db_path
        self.retention_days = max(1, min(retention_days, 3650))
        self._lock = Lock()
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        with self._connect() as conn:
            conn.executescript(_DDL)
            conn.execute("PRAGMA optimize")
        harden_private_file(self.db_path)

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys = ON")
        return conn

    def _latest_snapshot(
        self, conn: sqlite3.Connection, device_id: str
    ) -> tuple[datetime | None, dict[str, sqlite3.Row]]:
        latest = conn.execute(
            """SELECT snapshot_id, timestamp
               FROM device_observations
               WHERE device_id = ?
               ORDER BY id DESC LIMIT 1""",
            (device_id,),
        ).fetchone()
        if latest is None:
            return None, {}
        rows = conn.execute(
            "SELECT * FROM interface_observations WHERE snapshot_id = ?",
            (latest["snapshot_id"],),
        ).fetchall()
        return datetime.fromisoformat(latest["timestamp"]), {row["port"]: row for row in rows}

    @staticmethod
    def _derive_delta(
        current: InterfaceStatus,
        previous: sqlite3.Row | None,
        error_total: int,
        poe_state: str,
    ) -> InterfaceDelta:
        previous_errors = int(previous["total_errors"]) if previous is not None else None
        error_delta, counter_state = calculate_counter_delta(previous_errors, error_total)
        return InterfaceDelta(
            port=current.port,
            previousTotalErrors=previous_errors,
            currentTotalErrors=error_total,
            errorDelta=error_delta,
            counterState=counter_state,
            statusBefore=previous["status"] if previous is not None else None,
            statusAfter=current.status,
            adminBefore=previous["admin_state"] if previous is not None else None,
            adminAfter=interface_admin_state(current.status),
            speedBefore=previous["speed"] if previous is not None else None,
            speedAfter=current.speed,
            duplexBefore=previous["duplex"] if previous is not None else None,
            duplexAfter=current.duplex,
            vlanBefore=previous["vlan"] if previous is not None else None,
            vlanAfter=current.vlan,
            poeBefore=previous["poe_state"] if previous is not None else None,
            poeAfter=poe_state,
        )

    @staticmethod
    def _event(
        *,
        observed_at: datetime,
        device_id: str,
        interface: str,
        event_type: str,
        severity: str,
        title: str,
        detail: str,
        metadata: dict | None = None,
    ) -> NetworkEvent:
        return NetworkEvent(
            timestamp=observed_at,
            deviceId=device_id,
            interface=interface,
            eventType=event_type,
            severity=severity,
            title=title,
            detail=detail,
            metadata=metadata or {},
        )

    @classmethod
    def _derive_events(
        cls,
        *,
        observed_at: datetime,
        device_id: str,
        current: InterfaceStatus,
        previous: sqlite3.Row,
        delta: InterfaceDelta,
        learned_devices: list[str],
    ) -> list[NetworkEvent]:
        events: list[NetworkEvent] = []
        port = current.port
        description = current.name.lower()
        if delta.admin_before != delta.admin_after:
            title = f"{port} was administratively disabled" if delta.admin_after == "down" else f"{port} was administratively enabled"
            events.append(cls._event(
                observed_at=observed_at,
                device_id=device_id,
                interface=port,
                event_type="administrative_state_changed",
                severity="NOTICE",
                title=title,
                detail=f"Observed administrative state changed from {delta.admin_before} to {delta.admin_after}.",
                metadata={"before": delta.admin_before, "after": delta.admin_after},
            ))
        elif delta.status_before != delta.status_after:
            if delta.status_after == "connected":
                events.append(cls._event(
                    observed_at=observed_at,
                    device_id=device_id,
                    interface=port,
                    event_type="interface_link_up",
                    severity="HEALTHY",
                    title=f"{port} link established",
                    detail=f"The interface is connected at {current.speed or 'an unknown speed'} {current.duplex or 'with unknown duplex'}.",
                    metadata={"speed": current.speed, "duplex": current.duplex},
                ))
            elif delta.status_before == "connected":
                uplink = any(word in description for word in ("uplink", "router", "gateway"))
                events.append(cls._event(
                    observed_at=observed_at,
                    device_id=device_id,
                    interface=port,
                    event_type="interface_link_down",
                    severity="CRITICAL" if uplink else "NOTICE",
                    title=f"{port} link lost",
                    detail="The switch no longer detects an Ethernet link on this interface.",
                    metadata={"previousStatus": delta.status_before, "currentStatus": delta.status_after},
                ))

        if (
            delta.status_before == "connected"
            and delta.status_after == "connected"
            and delta.speed_before
            and _normalize_speed(delta.speed_before) != _normalize_speed(delta.speed_after)
        ):
            events.append(cls._event(
                observed_at=observed_at,
                device_id=device_id,
                interface=port,
                event_type="speed_changed",
                severity="NOTICE",
                title=f"{port} link speed changed",
                detail=f"Negotiated speed changed from {delta.speed_before} to {delta.speed_after}.",
                metadata={"before": delta.speed_before, "after": delta.speed_after},
            ))
        if (
            delta.status_before == "connected"
            and delta.status_after == "connected"
            and delta.duplex_before
            and _normalize_duplex(delta.duplex_before) != _normalize_duplex(delta.duplex_after)
        ):
            events.append(cls._event(
                observed_at=observed_at,
                device_id=device_id,
                interface=port,
                event_type="duplex_changed",
                severity="ATTENTION" if "half" in delta.duplex_after.lower() else "NOTICE",
                title=f"{port} duplex changed",
                detail=f"Negotiated duplex changed from {delta.duplex_before} to {delta.duplex_after}.",
                metadata={"before": delta.duplex_before, "after": delta.duplex_after},
            ))
        if delta.vlan_before is not None and delta.vlan_before != delta.vlan_after:
            events.append(cls._event(
                observed_at=observed_at,
                device_id=device_id,
                interface=port,
                event_type="vlan_changed",
                severity="NOTICE",
                title=f"{port} VLAN observation changed",
                detail=f"Observed access VLAN changed from {delta.vlan_before or 'unknown'} to {delta.vlan_after or 'unknown'}.",
                metadata={"before": delta.vlan_before, "after": delta.vlan_after},
            ))
        if delta.counter_state in {"increased", "wrapped"} and (delta.error_delta or 0) > 0:
            amount = int(delta.error_delta or 0)
            severity = "CRITICAL" if amount >= 100 else "ATTENTION" if amount >= 10 else "NOTICE"
            events.append(cls._event(
                observed_at=observed_at,
                device_id=device_id,
                interface=port,
                event_type="interface_errors_increased",
                severity=severity,
                title=f"{port} errors increased by {amount}",
                detail=f"The cumulative interface error counter rose by {amount} since the previous observation.",
                metadata={"delta": amount, "counterState": delta.counter_state},
            ))
        elif delta.counter_state == "reset":
            events.append(cls._event(
                observed_at=observed_at,
                device_id=device_id,
                interface=port,
                event_type="interface_counter_reset",
                severity="NOTICE",
                title=f"{port} error counter reset observed",
                detail="The cumulative error counter decreased. This can follow a restart or an external counter clear; SwitchOps cannot determine which.",
            ))

        if delta.poe_before is not None and _poe_active(delta.poe_before) != _poe_active(delta.poe_after):
            active = _poe_active(delta.poe_after)
            events.append(cls._event(
                observed_at=observed_at,
                device_id=device_id,
                interface=port,
                event_type="poe_state_changed",
                severity="HEALTHY" if active else "NOTICE",
                title=f"PoE {'detected' if active else 'stopped'} on {port}",
                detail=f"Observed PoE operational state changed from {delta.poe_before or 'unknown'} to {delta.poe_after or 'unknown'}.",
                metadata={"before": delta.poe_before, "after": delta.poe_after},
            ))

        # MAC-table membership churns constantly as entries age out and return,
        # especially behind an uplink. Reporting one event per address buried
        # everything else, so the whole change on an interface is reported as a
        # single event carrying the counts.
        previous_devices = set(json.loads(previous["learned_devices"] or "[]"))
        current_devices = set(learned_devices)
        appeared = sorted(current_devices - previous_devices)
        departed = sorted(previous_devices - current_devices)
        if appeared or departed:
            parts: list[str] = []
            if appeared:
                parts.append(f"{len(appeared)} appeared")
            if departed:
                parts.append(f"{len(departed)} no longer present")
            events.append(cls._event(
                observed_at=observed_at,
                device_id=device_id,
                interface=port,
                event_type="learned_addresses_changed",
                severity="HEALTHY" if appeared and not departed else "NOTICE",
                title=f"Learned addresses changed on {port} ({', '.join(parts)})",
                detail=(
                    f"The set of MAC-table entries on {port} changed: "
                    f"{len(appeared)} appeared and {len(departed)} are no longer present. "
                    "Entries age out on their own schedule, so a departure is not proof "
                    "that a device disconnected, and an arrival proves only where traffic "
                    "was learned - not what the device is."
                ),
                metadata={
                    "appeared": len(appeared),
                    "departed": len(departed),
                    "total": len(current_devices),
                },
            ))
        return events

    def record_snapshot(
        self,
        *,
        device_id: str,
        reachable: bool,
        cpu: CpuStatus,
        memory: MemoryStatus,
        environment: EnvironmentStatus,
        poe: PoeResponse,
        interfaces: Iterable[InterfaceStatus],
        errors: Iterable[InterfaceErrorCounters],
        mac_entries: Iterable[MacTableEntry],
        observed_at: datetime | None = None,
    ) -> TelemetrySnapshotSummary:
        observed_at = observed_at or datetime.now(timezone.utc)
        if observed_at.tzinfo is None:
            observed_at = observed_at.replace(tzinfo=timezone.utc)
        snapshot_id = uuid4().hex
        current_interfaces = list(interfaces)
        error_by_port = {counter.port: counter.total for counter in errors}
        poe_by_port: dict[str, PoePort] = {port.interface: port for port in poe.ports}
        devices_by_port: dict[str, list[str]] = {}
        for entry in mac_entries:
            if entry.port.upper() == "CPU" or entry.vlan.lower() == "all":
                continue
            # Persist only a one-way local identifier in telemetry/event data.
            # The live topology may still display the local-only MAC table.
            import hashlib
            normalized = "".join(char for char in entry.mac.lower() if char in "0123456789abcdef")
            key = hashlib.sha256(normalized.encode("ascii", errors="ignore")).hexdigest()[:12]
            devices_by_port.setdefault(entry.port, []).append(key)

        with self._lock, self._connect() as conn:
            previous_at, previous_rows = self._latest_snapshot(conn, device_id)
            deltas: list[InterfaceDelta] = []
            pending_events: list[NetworkEvent] = []
            for current in current_interfaces:
                previous = previous_rows.get(current.port)
                poe_state = poe_by_port[current.port].oper if current.port in poe_by_port else "not-supported"
                delta = self._derive_delta(
                    current,
                    previous,
                    error_by_port.get(current.port, 0),
                    poe_state,
                )
                deltas.append(delta)
                if previous is not None:
                    pending_events.extend(self._derive_events(
                        observed_at=observed_at,
                        device_id=device_id,
                        current=current,
                        previous=previous,
                        delta=delta,
                        learned_devices=devices_by_port.get(current.port, []),
                    ))

            conn.execute(
                """INSERT INTO device_observations
                   (snapshot_id, device_id, timestamp, reachable, cpu_5sec,
                    memory_used_pct, temperature_c, poe_used_w, poe_available_w)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    snapshot_id,
                    device_id,
                    observed_at.isoformat(),
                    1 if reachable else 0,
                    cpu.cpu_5sec,
                    _memory_used_pct(memory),
                    environment.temperature_c,
                    poe.used_watts,
                    poe.available_watts,
                ),
            )
            for current in current_interfaces:
                poe_state = poe_by_port[current.port].oper if current.port in poe_by_port else "not-supported"
                conn.execute(
                    """INSERT INTO interface_observations
                       (snapshot_id, device_id, port, status, admin_state, speed,
                        duplex, vlan, poe_state, total_errors, learned_devices)
                       VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                    (
                        snapshot_id,
                        device_id,
                        current.port,
                        current.status,
                        interface_admin_state(current.status),
                        current.speed,
                        current.duplex,
                        current.vlan,
                        poe_state,
                        error_by_port.get(current.port, 0),
                        json.dumps(sorted(devices_by_port.get(current.port, []))),
                    ),
                )
            for event in pending_events:
                cursor = conn.execute(
                    """INSERT INTO network_events
                       (timestamp, device_id, interface, event_type, severity,
                        title, detail, metadata)
                       VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
                    (
                        event.timestamp.isoformat(),
                        event.device_id,
                        event.interface,
                        event.event_type,
                        event.severity,
                        event.title,
                        event.detail,
                        json.dumps(event.metadata, sort_keys=True),
                    ),
                )
                event.id = cursor.lastrowid

            cutoff = (observed_at - timedelta(days=self.retention_days)).isoformat()
            conn.execute("DELETE FROM network_events WHERE timestamp < ?", (cutoff,))
            conn.execute("DELETE FROM device_observations WHERE timestamp < ?", (cutoff,))
            conn.commit()

        return TelemetrySnapshotSummary(
            observedAt=observed_at,
            previousObservedAt=previous_at,
            historyAvailable=previous_at is not None,
            interfaceDeltas=deltas,
            retentionDays=self.retention_days,
        )

    def recent_events(
        self,
        *,
        limit: int = 100,
        device_id: str | None = None,
        interface: str | None = None,
        severity: str | None = None,
        event_type: str | None = None,
    ) -> list[NetworkEvent]:
        clauses: list[str] = []
        params: list[object] = []
        if device_id:
            clauses.append("device_id = ?")
            params.append(device_id)
        if interface:
            clauses.append("interface = ?")
            params.append(interface)
        if severity:
            clauses.append("severity = ?")
            params.append(severity)
        if event_type:
            clauses.append("event_type = ?")
            params.append(event_type)
        where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
        params.append(max(1, min(limit, 500)))
        with self._lock, self._connect() as conn:
            rows = conn.execute(
                f"SELECT * FROM network_events {where} ORDER BY id DESC LIMIT ?",  # noqa: S608 -- clauses are constant strings
                params,
            ).fetchall()
        return [
            NetworkEvent(
                id=row["id"],
                timestamp=datetime.fromisoformat(row["timestamp"]),
                deviceId=row["device_id"],
                interface=row["interface"],
                eventType=row["event_type"],
                severity=row["severity"],
                title=row["title"],
                detail=row["detail"],
                metadata=json.loads(row["metadata"]),
            )
            for row in rows
        ]

    def record_event(self, event: NetworkEvent) -> NetworkEvent:
        """Persist a meaningful event derived by another local observer."""
        with self._lock, self._connect() as conn:
            cursor = conn.execute(
                """INSERT INTO network_events
                   (timestamp, device_id, interface, event_type, severity,
                    title, detail, metadata)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    event.timestamp.isoformat(),
                    event.device_id,
                    event.interface,
                    event.event_type,
                    event.severity,
                    event.title,
                    event.detail,
                    json.dumps(event.metadata, sort_keys=True),
                ),
            )
            conn.commit()
            event.id = cursor.lastrowid
        return event

    def history(
        self,
        *,
        device_id: str,
        since: datetime,
        limit: int = 500,
    ) -> TelemetryHistoryResponse:
        with self._lock, self._connect() as conn:
            rows = conn.execute(
                """SELECT timestamp, reachable, cpu_5sec, memory_used_pct,
                          temperature_c, poe_used_w, poe_available_w
                   FROM device_observations
                   WHERE device_id = ? AND timestamp >= ?
                   ORDER BY timestamp ASC LIMIT ?""",
                (device_id, since.isoformat(), max(1, min(limit, 2000))),
            ).fetchall()
        return TelemetryHistoryResponse(
            deviceId=device_id,
            observations=[
                DeviceObservationPoint(
                    timestamp=datetime.fromisoformat(row["timestamp"]),
                    reachable=bool(row["reachable"]),
                    cpu5Sec=row["cpu_5sec"],
                    memoryUsedPct=row["memory_used_pct"],
                    temperatureC=row["temperature_c"],
                    poeUsedW=row["poe_used_w"],
                    poeAvailableW=row["poe_available_w"],
                )
                for row in rows
            ],
        )


_store: Optional[TelemetryStore] = None


def get_telemetry_store(*, retention_days: int = 30) -> TelemetryStore:
    global _store
    if _store is None:
        _store = TelemetryStore(retention_days=retention_days)
    return _store
