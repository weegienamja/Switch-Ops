"""Durable, append-only SQLite research storage for EWPS experiments."""
from __future__ import annotations

from collections import defaultdict
from datetime import datetime, timezone
import csv
import io
import json
from pathlib import Path
import sqlite3
from threading import RLock
import uuid

from .config import get_settings
from .ewps_models import (
    DecisionPoint,
    EWPSConfig,
    ExperimentCreateRequest,
    ExperimentSession,
    ExperimentSummary,
    ExperimentTimeline,
)


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _json(value) -> str:
    if hasattr(value, "model_dump"):
        value = value.model_dump(by_alias=True, mode="json")
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True)


class EWPSResearchStore:
    """One local database whose material calculation rows are immutable."""

    def __init__(self, path: Path | None = None) -> None:
        self.path = path or (get_settings().data_dir / "ewps-research.sqlite3")
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = RLock()
        self._migrate()

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.path, timeout=10.0)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys = ON")
        connection.execute("PRAGMA busy_timeout = 5000")
        return connection

    def _migrate(self) -> None:
        with self._lock, self._connect() as connection:
            connection.executescript(
                """
                PRAGMA journal_mode = WAL;
                CREATE TABLE IF NOT EXISTS ewps_experiments (
                    experiment_id TEXT PRIMARY KEY,
                    name TEXT NOT NULL,
                    workload_label TEXT NOT NULL,
                    status TEXT NOT NULL CHECK(status IN ('CREATED','RUNNING','PAUSED','COMPLETED')),
                    kind TEXT NOT NULL CHECK(kind IN ('live','simulator')),
                    mode TEXT NOT NULL CHECK(mode = 'SHADOW'),
                    model_version TEXT NOT NULL,
                    release_id TEXT NOT NULL,
                    config_json TEXT NOT NULL,
                    candidate_path_ids_json TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    started_at TEXT,
                    ended_at TEXT,
                    paused_at TEXT
                );
                CREATE TABLE IF NOT EXISTS ewps_observations (
                    observation_id INTEGER PRIMARY KEY AUTOINCREMENT,
                    experiment_id TEXT NOT NULL REFERENCES ewps_experiments(experiment_id),
                    decision_index INTEGER NOT NULL,
                    timestamp TEXT NOT NULL,
                    path_id TEXT NOT NULL,
                    input_json TEXT NOT NULL,
                    calculation_json TEXT NOT NULL,
                    UNIQUE(experiment_id, decision_index, path_id)
                );
                CREATE TABLE IF NOT EXISTS ewps_decisions (
                    decision_id INTEGER PRIMARY KEY AUTOINCREMENT,
                    experiment_id TEXT NOT NULL REFERENCES ewps_experiments(experiment_id),
                    decision_index INTEGER NOT NULL,
                    timestamp TEXT NOT NULL,
                    decision_json TEXT NOT NULL,
                    UNIQUE(experiment_id, decision_index)
                );
                CREATE INDEX IF NOT EXISTS idx_ewps_observations_experiment_decision
                    ON ewps_observations(experiment_id, decision_index);
                CREATE INDEX IF NOT EXISTS idx_ewps_decisions_experiment_timestamp
                    ON ewps_decisions(experiment_id, timestamp);
                CREATE TRIGGER IF NOT EXISTS ewps_observations_immutable_update
                    BEFORE UPDATE ON ewps_observations BEGIN
                        SELECT RAISE(ABORT, 'EWPS observations are immutable');
                    END;
                CREATE TRIGGER IF NOT EXISTS ewps_observations_immutable_delete
                    BEFORE DELETE ON ewps_observations BEGIN
                        SELECT RAISE(ABORT, 'EWPS observations are immutable');
                    END;
                CREATE TRIGGER IF NOT EXISTS ewps_decisions_immutable_update
                    BEFORE UPDATE ON ewps_decisions BEGIN
                        SELECT RAISE(ABORT, 'EWPS decisions are immutable');
                    END;
                CREATE TRIGGER IF NOT EXISTS ewps_decisions_immutable_delete
                    BEFORE DELETE ON ewps_decisions BEGIN
                        SELECT RAISE(ABORT, 'EWPS decisions are immutable');
                    END;
                PRAGMA user_version = 1;
                PRAGMA optimize;
                """
            )

    def create(self, request: ExperimentCreateRequest, *, kind: str = "live") -> ExperimentSession:
        experiment_id = f"ewps-{uuid.uuid4()}"
        created = _now()
        with self._lock, self._connect() as connection:
            connection.execute(
                """
                INSERT INTO ewps_experiments (
                    experiment_id, name, workload_label, status, kind, mode,
                    model_version, release_id, config_json,
                    candidate_path_ids_json, created_at
                ) VALUES (?, ?, ?, 'CREATED', ?, 'SHADOW', '0.1.0',
                          'ewps-v0.1.0-alpha', ?, ?, ?)
                """,
                (
                    experiment_id,
                    request.name,
                    request.workload_label,
                    kind,
                    _json(request.config),
                    _json(request.candidate_path_ids),
                    created.isoformat(),
                ),
            )
        return self.get(experiment_id)

    def transition(self, experiment_id: str, status: str) -> ExperimentSession:
        current = self.get(experiment_id)
        allowed = {
            "CREATED": {"RUNNING", "COMPLETED"},
            "RUNNING": {"PAUSED", "COMPLETED"},
            "PAUSED": {"RUNNING", "COMPLETED"},
            "COMPLETED": set(),
        }
        if status not in allowed[current.status]:
            raise ValueError(f"Cannot transition experiment from {current.status} to {status}.")
        now = _now().isoformat()
        fields: dict[str, str | None] = {"status": status}
        if status == "RUNNING":
            if current.started_at is None:
                fields["started_at"] = now
            fields["paused_at"] = None
        elif status == "PAUSED":
            fields["paused_at"] = now
        elif status == "COMPLETED":
            fields["ended_at"] = now
            fields["paused_at"] = None
        assignments = ", ".join(f"{key} = ?" for key in fields)
        with self._lock, self._connect() as connection:
            connection.execute(
                f"UPDATE ewps_experiments SET {assignments} WHERE experiment_id = ?",
                (*fields.values(), experiment_id),
            )
        return self.get(experiment_id)

    def append(self, experiment_id: str, point: DecisionPoint) -> None:
        session = self.get(experiment_id)
        if session.status != "RUNNING":
            raise ValueError("EWPS observations can only be appended to a running experiment.")
        decision_payload = point.model_dump(by_alias=True, mode="json")
        decision_payload["calculations"] = []
        with self._lock, self._connect() as connection:
            for calculation in point.calculations:
                input_payload = {
                    "raw": calculation.raw.model_dump(by_alias=True, mode="json"),
                    "evidence": calculation.evidence.model_dump(by_alias=True, mode="json"),
                }
                connection.execute(
                    """
                    INSERT INTO ewps_observations (
                        experiment_id, decision_index, timestamp, path_id,
                        input_json, calculation_json
                    ) VALUES (?, ?, ?, ?, ?, ?)
                    """,
                    (
                        experiment_id,
                        point.decision_index,
                        point.timestamp.isoformat(),
                        calculation.path_id,
                        _json(input_payload),
                        _json(calculation),
                    ),
                )
            connection.execute(
                """
                INSERT INTO ewps_decisions (
                    experiment_id, decision_index, timestamp, decision_json
                ) VALUES (?, ?, ?, ?)
                """,
                (
                    experiment_id,
                    point.decision_index,
                    point.timestamp.isoformat(),
                    _json(decision_payload),
                ),
            )

    def get(self, experiment_id: str) -> ExperimentSession:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT * FROM ewps_experiments WHERE experiment_id = ?",
                (experiment_id,),
            ).fetchone()
            if row is None:
                raise KeyError(experiment_id)
            counts = connection.execute(
                """
                SELECT COUNT(*) AS measurements,
                       COUNT(DISTINCT decision_index) AS decision_points
                FROM ewps_observations WHERE experiment_id = ?
                """,
                (experiment_id,),
            ).fetchone()
        return ExperimentSession(
            experimentId=row["experiment_id"],
            name=row["name"],
            workloadLabel=row["workload_label"],
            status=row["status"],
            kind=row["kind"],
            mode=row["mode"],
            ewpsModelVersion=row["model_version"],
            releaseId=row["release_id"],
            config=EWPSConfig.model_validate_json(row["config_json"]),
            candidatePathIds=json.loads(row["candidate_path_ids_json"]),
            createdAt=row["created_at"],
            startedAt=row["started_at"],
            endedAt=row["ended_at"],
            pausedAt=row["paused_at"],
            totalMeasurements=int(counts["measurements"] or 0),
            decisionPoints=int(counts["decision_points"] or 0),
        )

    def list(self, limit: int = 50) -> list[ExperimentSession]:
        with self._connect() as connection:
            ids = [
                row["experiment_id"]
                for row in connection.execute(
                    "SELECT experiment_id FROM ewps_experiments ORDER BY created_at DESC LIMIT ?",
                    (max(1, min(limit, 500)),),
                ).fetchall()
            ]
        return [self.get(experiment_id) for experiment_id in ids]

    def timeline(self, experiment_id: str) -> ExperimentTimeline:
        session = self.get(experiment_id)
        with self._connect() as connection:
            decision_rows = connection.execute(
                """
                SELECT decision_index, decision_json FROM ewps_decisions
                WHERE experiment_id = ? ORDER BY decision_index
                """,
                (experiment_id,),
            ).fetchall()
            observation_rows = connection.execute(
                """
                SELECT decision_index, calculation_json FROM ewps_observations
                WHERE experiment_id = ? ORDER BY decision_index, path_id
                """,
                (experiment_id,),
            ).fetchall()
        calculations: dict[int, list[dict]] = defaultdict(list)
        for row in observation_rows:
            calculations[int(row["decision_index"])].append(json.loads(row["calculation_json"]))
        decisions: list[DecisionPoint] = []
        for row in decision_rows:
            payload = json.loads(row["decision_json"])
            payload["calculations"] = calculations[int(row["decision_index"])]
            decisions.append(DecisionPoint.model_validate(payload))
        return ExperimentTimeline(session=session, decisions=decisions)

    def summary(self, experiment_id: str) -> ExperimentSummary:
        timeline = self.timeline(experiment_id)
        session = timeline.session
        starts = session.started_at or session.created_at
        end = session.ended_at or (timeline.decisions[-1].timestamp if timeline.decisions else _now())
        duration = max(0.0, (end - starts).total_seconds())
        measurements: dict[str, int] = defaultdict(int)
        confidences: dict[str, list[float]] = defaultdict(list)
        ineligible: dict[str, int] = defaultdict(int)
        ineligible_seconds: dict[str, float] = defaultdict(float)
        preferred_seconds: dict[str, float] = defaultdict(float)
        disagreements = recommendation_changes = suppressed = 0
        stale = instability = failures = 0
        notable: list[str] = []
        observed_duration = 0.0
        for index, point in enumerate(timeline.decisions):
            next_at = (
                timeline.decisions[index + 1].timestamp
                if index + 1 < len(timeline.decisions)
                else end
            )
            point_seconds = max(0.0, (next_at - point.timestamp).total_seconds())
            observed_duration += point_seconds
            for calculation in point.calculations:
                measurements[calculation.path_id] += 1
                confidences[calculation.path_id].append(calculation.certainty.composite)
                if not calculation.eligible:
                    ineligible[calculation.path_id] += 1
                    ineligible_seconds[calculation.path_id] += point_seconds
                if not calculation.valid:
                    failures += 1
            if point.hysteresis.preferred_path_id:
                preferred_seconds[point.hysteresis.preferred_path_id] += point_seconds
            choices = {
                item.path_id
                for item in point.algorithms
                if item.algorithm in {"lowest_latency", "performance_only", "ewps", "ewps_hysteresis"}
                and item.path_id is not None
            }
            if len(choices) > 1:
                disagreements += 1
            if point.hysteresis.recommendation_changed:
                recommendation_changes += 1
            if point.hysteresis.suppressed:
                suppressed += 1
            stale += sum(event.startswith("telemetry_became_stale:") for event in point.events)
            instability += sum(event.startswith("variance_spike:") for event in point.events)
            if point.events and len(notable) < 50:
                notable.append(f"{point.timestamp.isoformat()} — {point.explanation}")
        points = len(timeline.decisions)
        path_ids = session.candidate_path_ids
        return ExperimentSummary(
            experimentId=experiment_id,
            durationSeconds=duration,
            totalSamples=sum(measurements.values()),
            decisionPoints=points,
            measurementsPerPath={path_id: measurements[path_id] for path_id in path_ids},
            averageConfidencePerPath={
                path_id: (
                    sum(confidences[path_id]) / len(confidences[path_id])
                    if confidences[path_id] else None
                )
                for path_id in path_ids
            },
            minimumConfidencePerPath={
                path_id: min(confidences[path_id]) if confidences[path_id] else None
                for path_id in path_ids
            },
            preferredPercentPerPath={
                path_id: (
                    preferred_seconds[path_id] / observed_duration * 100.0
                    if observed_duration else 0.0
                )
                for path_id in path_ids
            },
            algorithmDisagreementRate=(disagreements / points if points else 0.0),
            ewpsRecommendationChanges=recommendation_changes,
            hysteresisSuppressedChanges=suppressed,
            ineligibleSamplesPerPath={path_id: ineligible[path_id] for path_id in path_ids},
            ineligibleSecondsPerPath={path_id: ineligible_seconds[path_id] for path_id in path_ids},
            staleEvidenceEvents=stale,
            instabilityEvents=instability,
            telemetryFailures=failures,
            notableDecisionEvents=notable,
        )

    def privacy_safe_jsonl(self, experiment_id: str) -> str:
        timeline = self.timeline(experiment_id)
        config = timeline.session.config
        lines: list[str] = []
        for point in timeline.decisions:
            preferred = point.hysteresis.preferred_path_id
            for calculation in point.calculations:
                record = {
                    "timestamp": point.timestamp.isoformat(),
                    "experiment_id": experiment_id,
                    "ewps_version": calculation.model_version,
                    "path_id": calculation.path_id,
                    "workload_label": timeline.session.workload_label,
                    "raw": calculation.raw.model_dump(by_alias=False, mode="json"),
                    "evidence": calculation.evidence.model_dump(by_alias=False, mode="json"),
                    "certainty": calculation.certainty.model_dump(by_alias=False, mode="json"),
                    "model": {
                        "lambda": config.lambda_decay,
                        "k": config.density_k,
                        "alpha": config.alpha,
                        "p_min": config.p_min,
                        "certainty_mode": config.certainty_mode,
                        "weights": config.weights.model_dump(),
                    },
                    "cost": {"raw": calculation.raw_cost, "ewps": calculation.ewps_cost},
                    "eligibility": {"eligible": calculation.eligible, "reasons": calculation.reasons},
                    "algorithms": [item.model_dump(by_alias=False) for item in point.algorithms],
                    "decision": {
                        "preferred": calculation.path_id == preferred,
                        "would_switch": point.hysteresis.would_switch,
                        "reason": point.hysteresis.reason,
                        "switch_blocked_by": point.hysteresis.switch_blocked_by,
                    },
                }
                lines.append(_json(record))
        return "\n".join(lines) + ("\n" if lines else "")

    def privacy_safe_csv(self, experiment_id: str) -> str:
        timeline = self.timeline(experiment_id)
        output = io.StringIO(newline="")
        fields = [
            "timestamp", "experiment_id", "path_id", "latency_ms", "jitter_ms",
            "loss_pct", "sample_count", "age_seconds", "mean_ms", "stddev_ms",
            "effective_samples", "freshness", "stability", "density", "topology",
            "composite", "raw_cost", "ewps_cost", "eligible", "ewps_preferred",
            "hysteresis_preferred", "switch_suppressed", "switch_blocked_by",
        ]
        writer = csv.DictWriter(output, fieldnames=fields)
        writer.writeheader()
        for point in timeline.decisions:
            ewps = next((item.path_id for item in point.algorithms if item.algorithm == "ewps"), None)
            for item in point.calculations:
                writer.writerow({
                    "timestamp": point.timestamp.isoformat(),
                    "experiment_id": experiment_id,
                    "path_id": item.path_id,
                    "latency_ms": item.raw.latency_ms,
                    "jitter_ms": item.raw.jitter_ms,
                    "loss_pct": item.raw.loss_pct,
                    "sample_count": item.raw.sample_count,
                    "age_seconds": item.evidence.age_seconds,
                    "mean_ms": item.evidence.mean_ms,
                    "stddev_ms": item.evidence.stddev_ms,
                    "effective_samples": item.evidence.effective_samples,
                    "freshness": item.certainty.freshness,
                    "stability": item.certainty.stability,
                    "density": item.certainty.density,
                    "topology": item.certainty.topology,
                    "composite": item.certainty.composite,
                    "raw_cost": item.raw_cost,
                    "ewps_cost": item.ewps_cost,
                    "eligible": item.eligible,
                    "ewps_preferred": item.path_id == ewps,
                    "hysteresis_preferred": item.path_id == point.hysteresis.preferred_path_id,
                    "switch_suppressed": point.hysteresis.suppressed,
                    "switch_blocked_by": point.hysteresis.switch_blocked_by,
                })
        return output.getvalue()


_store: EWPSResearchStore | None = None


def get_ewps_store() -> EWPSResearchStore:
    global _store
    expected = get_settings().data_dir / "ewps-research.sqlite3"
    if _store is None or _store.path != expected:
        _store = EWPSResearchStore(expected)
    return _store
