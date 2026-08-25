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
from .ewps_v2_models import (
    DistributionSummary,
    EWPS_V2_RELEASE_ID,
    EWPSV2Config,
    V2CandidateSnapshot,
    V2DecisionPoint,
    V2ExperimentCreateRequest,
    V2ExperimentEvent,
    V2ExperimentSession,
    V2ExperimentSummary,
    V2ExperimentTimeline,
    V2PhaseSummary,
    V2ScenarioPhaseSnapshot,
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
                CREATE TABLE IF NOT EXISTS ewps_experiment_events (
                    event_id TEXT PRIMARY KEY,
                    experiment_id TEXT NOT NULL REFERENCES ewps_experiments(experiment_id),
                    timestamp TEXT NOT NULL,
                    event_type TEXT NOT NULL CHECK(event_type IN (
                        'SCENARIO_PHASE_CHANGED', 'SCENARIO_PHASE_APPLY_FAILED'
                    )),
                    event_json TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS idx_ewps_observations_experiment_decision
                    ON ewps_observations(experiment_id, decision_index);
                CREATE INDEX IF NOT EXISTS idx_ewps_decisions_experiment_timestamp
                    ON ewps_decisions(experiment_id, timestamp);
                CREATE INDEX IF NOT EXISTS idx_ewps_events_experiment_timestamp
                    ON ewps_experiment_events(experiment_id, timestamp, event_id);
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
                CREATE TRIGGER IF NOT EXISTS ewps_experiment_events_immutable_update
                    BEFORE UPDATE ON ewps_experiment_events BEGIN
                        SELECT RAISE(ABORT, 'EWPS experiment events are immutable');
                    END;
                CREATE TRIGGER IF NOT EXISTS ewps_experiment_events_immutable_delete
                    BEFORE DELETE ON ewps_experiment_events BEGIN
                        SELECT RAISE(ABORT, 'EWPS experiment events are immutable');
                    END;
                PRAGMA user_version = 4;
                PRAGMA optimize;
                """
            )
            columns = {
                row["name"]
                for row in connection.execute("PRAGMA table_info(ewps_experiments)").fetchall()
            }
            migrations = {
                "source_mode": "ALTER TABLE ewps_experiments ADD COLUMN source_mode TEXT",
                "candidate_snapshot_json": "ALTER TABLE ewps_experiments ADD COLUMN candidate_snapshot_json TEXT",
                "lab_instance_id": "ALTER TABLE ewps_experiments ADD COLUMN lab_instance_id TEXT",
                "lab_topology_version": "ALTER TABLE ewps_experiments ADD COLUMN lab_topology_version TEXT",
                "initial_verification_status": "ALTER TABLE ewps_experiments ADD COLUMN initial_verification_status TEXT",
                "controlled_scenario": "ALTER TABLE ewps_experiments ADD COLUMN controlled_scenario TEXT",
                "initial_phase_json": "ALTER TABLE ewps_experiments ADD COLUMN initial_phase_json TEXT",
            }
            for column, statement in migrations.items():
                if column not in columns:
                    connection.execute(statement)
            connection.executescript(
                """
                DROP TRIGGER IF EXISTS ewps_source_binding_immutable;
                CREATE TRIGGER ewps_source_binding_immutable
                BEFORE UPDATE ON ewps_experiments
                WHEN NEW.source_mode IS NOT OLD.source_mode
                  OR NEW.candidate_path_ids_json IS NOT OLD.candidate_path_ids_json
                  OR NEW.candidate_snapshot_json IS NOT OLD.candidate_snapshot_json
                  OR NEW.lab_instance_id IS NOT OLD.lab_instance_id
                  OR NEW.lab_topology_version IS NOT OLD.lab_topology_version
                  OR NEW.initial_verification_status IS NOT OLD.initial_verification_status
                  OR NEW.controlled_scenario IS NOT OLD.controlled_scenario
                  OR NEW.initial_phase_json IS NOT OLD.initial_phase_json
                BEGIN
                    SELECT RAISE(ABORT, 'EWPS source binding is immutable');
                END;
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

    def create_v2(
        self,
        request: V2ExperimentCreateRequest,
        *,
        candidate_snapshot: list[V2CandidateSnapshot],
        lab_instance_id: str | None,
        lab_topology_version: str | None,
        initial_verification_status: str,
        controlled_scenario: str | None,
        initial_scenario_phase: V2ScenarioPhaseSnapshot | None = None,
        kind: str = "live",
    ) -> V2ExperimentSession:
        experiment_id = f"ewps-{uuid.uuid4()}"
        created = _now()
        with self._lock, self._connect() as connection:
            connection.execute(
                """
                INSERT INTO ewps_experiments (
                    experiment_id, name, workload_label, status, kind, mode,
                    model_version, release_id, config_json,
                    candidate_path_ids_json, created_at, source_mode,
                    candidate_snapshot_json, lab_instance_id, lab_topology_version,
                    initial_verification_status, controlled_scenario, initial_phase_json
                ) VALUES (?, ?, ?, 'CREATED', ?, 'SHADOW', '0.2.0',
                          ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    experiment_id,
                    request.name,
                    request.workload_label,
                    kind,
                    EWPS_V2_RELEASE_ID,
                    _json(request.config),
                    _json(request.candidate_path_ids),
                    created.isoformat(),
                    request.source_mode,
                    _json([item.model_dump(by_alias=True, mode="json") for item in candidate_snapshot]),
                    lab_instance_id,
                    lab_topology_version,
                    initial_verification_status,
                    controlled_scenario,
                    _json(initial_scenario_phase) if initial_scenario_phase is not None else None,
                ),
            )
        session = self.get(experiment_id)
        if not isinstance(session, V2ExperimentSession):
            raise RuntimeError("The v0.2 experiment was stored with an invalid model version.")
        return session

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
        if isinstance(session, V2ExperimentSession) and session.release_id == EWPS_V2_RELEASE_ID:
            phase = point.scenario_phase if isinstance(point, V2DecisionPoint) else None
            if session.source_mode == "CONTROLLED_DUAL_PATH":
                if phase is None:
                    raise ValueError("Schema-v4 controlled observations require an authoritative phase snapshot.")
                if (
                    phase.scenario_id != session.controlled_impairment_scenario
                    or phase.lab_instance_id != session.lab_instance_id
                ):
                    raise ValueError("The observation phase does not match the immutable controlled-lab binding.")
            elif phase is not None:
                raise ValueError("Non-controlled observations cannot contain an impairment phase.")
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

    def append_event(self, event: V2ExperimentEvent) -> None:
        session = self.get(event.experiment_id)
        if not isinstance(session, V2ExperimentSession) or session.status != "RUNNING":
            raise ValueError("EWPS phase events can only be appended to a running v0.2 experiment.")
        if (
            session.source_mode != "CONTROLLED_DUAL_PATH"
            or event.scenario_id != session.controlled_impairment_scenario
            or event.lab_instance_id != session.lab_instance_id
        ):
            raise ValueError("The phase event does not match the immutable controlled-lab binding.")
        timeline = self.timeline(event.experiment_id)
        if not isinstance(timeline, V2ExperimentTimeline) or timeline.session.initial_scenario_phase is None:
            raise ValueError("The controlled experiment has no authoritative initial phase.")
        successful = [item for item in timeline.events if item.application_succeeded]
        current_index = (
            successful[-1].new_phase_index
            if successful else timeline.session.initial_scenario_phase.phase_index
        )
        current_id = (
            successful[-1].new_phase_id
            if successful else timeline.session.initial_scenario_phase.phase_id
        )
        if event.previous_phase_index != current_index or event.previous_phase_id != current_id:
            raise ValueError("The phase event does not continue the recorded authoritative timeline.")
        with self._lock, self._connect() as connection:
            connection.execute(
                """
                INSERT INTO ewps_experiment_events (
                    event_id, experiment_id, timestamp, event_type, event_json
                ) VALUES (?, ?, ?, ?, ?)
                """,
                (
                    event.event_id,
                    event.experiment_id,
                    event.timestamp.isoformat(),
                    event.event_type,
                    _json(event),
                ),
            )

    def get(self, experiment_id: str) -> ExperimentSession | V2ExperimentSession:
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
        common = {
            "experimentId": row["experiment_id"],
            "name": row["name"],
            "workloadLabel": row["workload_label"],
            "status": row["status"],
            "kind": row["kind"],
            "mode": row["mode"],
            "ewpsModelVersion": row["model_version"],
            "releaseId": row["release_id"],
            "candidatePathIds": json.loads(row["candidate_path_ids_json"]),
            "createdAt": row["created_at"],
            "startedAt": row["started_at"],
            "endedAt": row["ended_at"],
            "pausedAt": row["paused_at"],
            "totalMeasurements": int(counts["measurements"] or 0),
            "decisionPoints": int(counts["decision_points"] or 0),
        }
        if row["model_version"] == "0.1.0":
            return ExperimentSession(
                **common,
                config=EWPSConfig.model_validate_json(row["config_json"]),
            )
        if row["model_version"] == "0.2.0":
            snapshot_json = row["candidate_snapshot_json"]
            return V2ExperimentSession(
                **common,
                config=EWPSV2Config.model_validate_json(row["config_json"]),
                sourceMode=row["source_mode"] or "LEGACY_UNBOUND",
                candidateSnapshot=(json.loads(snapshot_json) if snapshot_json else []),
                labInstanceId=row["lab_instance_id"],
                labTopologyVersion=row["lab_topology_version"],
                initialVerificationStatus=row["initial_verification_status"] or "LEGACY_UNKNOWN",
                controlledImpairmentScenario=row["controlled_scenario"],
                initialScenarioPhase=(
                    json.loads(row["initial_phase_json"])
                    if row["initial_phase_json"] else None
                ),
            )
        raise ValueError(f"Unsupported stored EWPS model version: {row['model_version']}")

    def list(self, limit: int = 50) -> list[ExperimentSession | V2ExperimentSession]:
        with self._connect() as connection:
            ids = [
                row["experiment_id"]
                for row in connection.execute(
                    "SELECT experiment_id FROM ewps_experiments ORDER BY created_at DESC LIMIT ?",
                    (max(1, min(limit, 500)),),
                ).fetchall()
            ]
        return [self.get(experiment_id) for experiment_id in ids]

    def timeline(self, experiment_id: str) -> ExperimentTimeline | V2ExperimentTimeline:
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
            event_rows = connection.execute(
                """
                SELECT event_json FROM ewps_experiment_events
                WHERE experiment_id = ? ORDER BY timestamp, event_id
                """,
                (experiment_id,),
            ).fetchall()
        calculations: dict[int, list[dict]] = defaultdict(list)
        for row in observation_rows:
            calculations[int(row["decision_index"])].append(json.loads(row["calculation_json"]))
        if session.ewps_model_version == "0.1.0":
            decisions: list[DecisionPoint] = []
            for row in decision_rows:
                payload = json.loads(row["decision_json"])
                payload["calculations"] = calculations[int(row["decision_index"])]
                decisions.append(DecisionPoint.model_validate(payload))
            return ExperimentTimeline(session=session, decisions=decisions)
        decisions_v2: list[V2DecisionPoint] = []
        for row in decision_rows:
            payload = json.loads(row["decision_json"])
            payload["calculations"] = calculations[int(row["decision_index"])]
            decisions_v2.append(V2DecisionPoint.model_validate(payload))
        if not isinstance(session, V2ExperimentSession):
            raise ValueError("Stored v0.2 timeline has an incompatible session record.")
        return V2ExperimentTimeline(
            session=session,
            decisions=decisions_v2,
            events=[V2ExperimentEvent.model_validate(json.loads(row["event_json"])) for row in event_rows],
        )

    def summary(self, experiment_id: str) -> ExperimentSummary | V2ExperimentSummary:
        session = self.get(experiment_id)
        if session.ewps_model_version == "0.1.0":
            return self._summary_v1(experiment_id)
        return self._summary_v2(experiment_id)

    def _summary_v1(self, experiment_id: str) -> ExperimentSummary:
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

    @staticmethod
    def _distribution(values: list[float]) -> DistributionSummary:
        if not values:
            return DistributionSummary()
        ordered = sorted(values)
        middle = len(ordered) // 2
        median = (
            ordered[middle]
            if len(ordered) % 2
            else (ordered[middle - 1] + ordered[middle]) / 2.0
        )
        return DistributionSummary(
            minimum=ordered[0],
            mean=sum(ordered) / len(ordered),
            median=median,
            maximum=ordered[-1],
        )

    def _phase_summaries(
        self,
        timeline: V2ExperimentTimeline,
        experiment_end: datetime,
    ) -> list[V2PhaseSummary]:
        initial = timeline.session.initial_scenario_phase
        if initial is None:
            return []
        experiment_start = timeline.session.started_at or timeline.session.created_at
        phases: list[tuple[V2ScenarioPhaseSnapshot, datetime]] = [(initial, experiment_start)]
        for event in timeline.events:
            if not event.application_succeeded or event.event_type != "SCENARIO_PHASE_CHANGED":
                continue
            phases.append((V2ScenarioPhaseSnapshot(
                scenarioId=event.scenario_id,
                phaseIndex=event.new_phase_index,
                phaseId=event.new_phase_id,
                labInstanceId=event.lab_instance_id,
                pathProfiles=event.path_profiles,
            ), event.timestamp))

        summaries: list[V2PhaseSummary] = []
        algorithms = ["lowest_latency", "lowest_loss", "performance_only", "ewps", "ewps_hysteresis"]
        for phase_position, (phase, phase_start) in enumerate(phases):
            phase_end = phases[phase_position + 1][1] if phase_position + 1 < len(phases) else experiment_end
            phase_end = max(phase_start, phase_end)
            points = [
                point for point in timeline.decisions
                if point.scenario_phase is not None
                and point.scenario_phase.scenario_id == phase.scenario_id
                and point.scenario_phase.phase_index == phase.phase_index
                and phase_start <= point.timestamp <= phase_end
            ]
            measurements: dict[str, int] = defaultdict(int)
            performance: dict[str, list[float]] = defaultdict(list)
            raw_costs: dict[str, list[float]] = defaultdict(list)
            ewps_costs: dict[str, list[float]] = defaultdict(list)
            preferences: dict[str, dict[str, int]] = {
                algorithm: defaultdict(int) for algorithm in algorithms
            }
            eligible_seconds: dict[str, float] = defaultdict(float)
            disagreement = suppressions = failures = stale = 0
            for point_index, point in enumerate(points):
                next_at = points[point_index + 1].timestamp if point_index + 1 < len(points) else phase_end
                point_seconds = max(0.0, (min(next_at, phase_end) - max(point.timestamp, phase_start)).total_seconds())
                for item in point.calculations:
                    measurements[item.path_id] += 1
                    performance[item.path_id].append(item.confidence.performance)
                    if item.raw_cost is not None:
                        raw_costs[item.path_id].append(item.raw_cost)
                    if item.ewps_cost is not None:
                        ewps_costs[item.path_id].append(item.ewps_cost)
                    if item.eligible:
                        eligible_seconds[item.path_id] += point_seconds
                    failures += int(item.raw.telemetry_state in {
                        "transient_failure", "candidate_unavailable", "controlled_lab_lost"
                    })
                    stale += int(item.raw.telemetry_state == "evidence_stale")
                choices = [choice.path_id for choice in point.algorithms if choice.path_id is not None]
                disagreement += int(len(set(choices)) > 1)
                for choice in point.algorithms:
                    if choice.path_id is not None:
                        preferences[choice.algorithm][choice.path_id] += 1
                suppressions += int(point.hysteresis.suppressed)
                stale += sum(
                    event.startswith("telemetry_became_stale:")
                    or event.startswith("evidence_staleness_injected:")
                    for event in point.events
                )
            path_ids = timeline.session.candidate_path_ids
            summaries.append(V2PhaseSummary(
                scenarioId=phase.scenario_id,
                phaseIndex=phase.phase_index,
                phaseId=phase.phase_id,
                startedAt=phase_start,
                endedAt=phase_end,
                durationSeconds=max(0.0, (phase_end - phase_start).total_seconds()),
                decisionPoints=len(points),
                measurementsPerPath={path_id: measurements[path_id] for path_id in path_ids},
                performanceConfidencePerPath={
                    path_id: self._distribution(performance[path_id]) for path_id in path_ids
                },
                rawCostDistributionPerPath={
                    path_id: self._distribution(raw_costs[path_id]) for path_id in path_ids
                },
                ewpsCostDistributionPerPath={
                    path_id: self._distribution(ewps_costs[path_id]) for path_id in path_ids
                },
                algorithmPreferenceCounts={
                    algorithm: dict(preferences[algorithm]) for algorithm in algorithms
                },
                algorithmDisagreementCount=disagreement,
                hysteresisSuppressions=suppressions,
                pathEligibilitySeconds={path_id: eligible_seconds[path_id] for path_id in path_ids},
                telemetryFailures=failures,
                staleEvents=stale,
            ))
        return summaries

    def _summary_v2(self, experiment_id: str) -> V2ExperimentSummary:
        timeline = self.timeline(experiment_id)
        if not isinstance(timeline, V2ExperimentTimeline):
            raise ValueError("A v0.2 summary requires a v0.2 timeline.")
        session = timeline.session
        starts = session.started_at or session.created_at
        end = session.ended_at or (timeline.decisions[-1].timestamp if timeline.decisions else _now())
        duration = max(0.0, (end - starts).total_seconds())
        algorithms = ["lowest_latency", "lowest_loss", "performance_only", "ewps", "ewps_hysteresis"]
        measurements: dict[str, int] = defaultdict(int)
        performance_confidence: dict[str, list[float]] = defaultdict(list)
        topology_confidences: dict[str, list[float]] = defaultdict(list)
        ewps_costs: dict[str, list[float]] = defaultdict(list)
        raw_costs: dict[str, list[float]] = defaultdict(list)
        below_seconds: dict[str, float] = defaultdict(float)
        preference_seconds: dict[str, dict[str, float]] = {
            algorithm: defaultdict(float) for algorithm in algorithms
        }
        previous_choices: dict[str, str | None] = {}
        switches: dict[str, int] = defaultdict(int)
        pairwise_counts: dict[str, dict[str, int]] = {
            left: {right: 0 for right in algorithms} for left in algorithms
        }
        usable_over_time: list[dict[str, Any]] = []
        ever_persistently_unavailable: set[str] = set()
        ever_viable: set[str] = set()
        unavailable_events = transient_failures = recoveries = 0
        disagreement_points = ewps_latency_differences = suppressed = 0
        rolling_loss_events = stale_events = 0
        disagreement_components: dict[str, int] = defaultdict(int)
        notable: list[str] = []
        observed_start_intervals: list[float] = []
        observed_collection_durations: list[float] = []
        cadence_overrun_count = 0

        for index, point in enumerate(timeline.decisions):
            next_at = timeline.decisions[index + 1].timestamp if index + 1 < len(timeline.decisions) else end
            point_seconds = max(0.0, (next_at - point.timestamp).total_seconds())
            by_path = {item.path_id: item for item in point.calculations}
            usable_count = sum(
                item.raw.routing_metrics_usable
                and item.raw.candidate_lifecycle in {"VIABLE", "RECOVERING"}
                for item in point.calculations
            )
            usable_over_time.append({"timestamp": point.timestamp.isoformat(), "count": usable_count})
            if point.cadence is not None:
                observed_collection_durations.append(point.cadence.collection_duration_ms)
                if point.cadence.actual_start_to_start_seconds is not None:
                    observed_start_intervals.append(point.cadence.actual_start_to_start_seconds)
                cadence_overrun_count = max(
                    cadence_overrun_count,
                    point.cadence.cadence_overrun_count,
                )
            for item in point.calculations:
                measurements[item.path_id] += 1
                performance_confidence[item.path_id].append(item.confidence.performance)
                topology_confidences[item.path_id].append(item.confidence.topology)
                if item.ewps_cost is not None:
                    ewps_costs[item.path_id].append(item.ewps_cost)
                if item.raw_cost is not None:
                    raw_costs[item.path_id].append(item.raw_cost)
                if item.confidence.performance < session.config.p_perf_min:
                    below_seconds[item.path_id] += point_seconds
                if item.raw.candidate_lifecycle == "PERSISTENTLY_UNAVAILABLE":
                    ever_persistently_unavailable.add(item.path_id)
                if item.raw.candidate_lifecycle in {"VIABLE", "RECOVERING"}:
                    ever_viable.add(item.path_id)
                unavailable_events += int(item.raw.candidate_unavailable_event)
                transient_failures += int(item.raw.transient_failure)
                recoveries += int(item.raw.recovery_event)

            choices = {item.algorithm: item.path_id for item in point.algorithms}
            non_null = {path_id for path_id in choices.values() if path_id is not None}
            disagreement_points += int(len(non_null) > 1)
            for algorithm in algorithms:
                path_id = choices.get(algorithm)
                if path_id is not None:
                    preference_seconds[algorithm][path_id] += point_seconds
                if algorithm in previous_choices and previous_choices[algorithm] != path_id:
                    switches[algorithm] += 1
                previous_choices[algorithm] = path_id
            for left in algorithms:
                for right in algorithms:
                    left_path, right_path = choices.get(left), choices.get(right)
                    if left_path is not None and right_path is not None and left_path != right_path:
                        pairwise_counts[left][right] += 1
            latency_path = choices.get("lowest_latency")
            ewps_path = choices.get("ewps")
            if latency_path is not None and ewps_path is not None and latency_path != ewps_path:
                ewps_latency_differences += 1
                faster = by_path.get(latency_path)
                if faster:
                    components = {
                        "freshness": faster.confidence.freshness,
                        "stability": faster.confidence.stability,
                        "density": faster.confidence.density,
                        "topology": faster.confidence.topology,
                    }
                    weakest = min(components, key=lambda key: (components[key], key))
                    disagreement_components[weakest] += 1
            suppressed += int(point.hysteresis.suppressed)
            rolling_loss_events += sum(event.startswith("rolling_loss_event:") for event in point.events)
            stale_events += sum(
                event.startswith("telemetry_became_stale:")
                or event.startswith("evidence_staleness_injected:")
                for event in point.events
            )
            if point.events and len(notable) < 80:
                notable.append(f"{point.timestamp.isoformat()} — {point.explanation}")

        points = len(timeline.decisions)
        path_ids = session.candidate_path_ids

        def statistics_for(values: list[float]) -> dict[str, float | None]:
            return {
                "minimum": min(values) if values else None,
                "average": sum(values) / len(values) if values else None,
                "maximum": max(values) if values else None,
            }

        return V2ExperimentSummary(
            experimentId=experiment_id,
            durationSeconds=duration,
            totalSamples=sum(measurements.values()),
            decisionPoints=points,
            configuredIntervalSeconds=session.config.sample_interval_seconds,
            observedStartToStartSeconds=self._distribution(observed_start_intervals),
            observedCollectionDurationMs=self._distribution(observed_collection_durations),
            cadenceOverrunCount=cadence_overrun_count,
            measurementsPerPath={path_id: measurements[path_id] for path_id in path_ids},
            usablePathCountOverTime=usable_over_time,
            unavailableCandidateCount=len(ever_persistently_unavailable - ever_viable),
            candidateUnavailableEvents=unavailable_events,
            transientFailuresOnViablePaths=transient_failures,
            recoveryEvents=recoveries,
            performanceConfidencePerPath={
                path_id: statistics_for(performance_confidence[path_id]) for path_id in path_ids
            },
            topologyConfidencePerPath={
                path_id: statistics_for(topology_confidences[path_id]) for path_id in path_ids
            },
            ewpsCostDistributionPerPath={
                path_id: self._distribution(ewps_costs[path_id]) for path_id in path_ids
            },
            rawCostDistributionPerPath={
                path_id: self._distribution(raw_costs[path_id]) for path_id in path_ids
            },
            algorithmDisagreementPercentage=(disagreement_points / points * 100.0 if points else 0.0),
            pairwiseDisagreementMatrix={
                left: {
                    right: (pairwise_counts[left][right] / points * 100.0 if points else 0.0)
                    for right in algorithms
                }
                for left in algorithms
            },
            preferenceDurationSecondsPerAlgorithmPath={
                algorithm: {path_id: preference_seconds[algorithm][path_id] for path_id in path_ids}
                for algorithm in algorithms
            },
            recommendationSwitchesPerAlgorithm={algorithm: switches[algorithm] for algorithm in algorithms},
            hysteresisSuppressedSwitches=suppressed,
            belowEvidenceThresholdSecondsPerPath={path_id: below_seconds[path_id] for path_id in path_ids},
            rollingLossEvents=rolling_loss_events,
            staleEvidenceEvents=stale_events,
            ewpsVsLowestLatencyDifferencePercentage=(
                ewps_latency_differences / points * 100.0 if points else 0.0
            ),
            disagreementEvidenceComponents=dict(disagreement_components),
            mostCommonDisagreementComponent=(
                max(disagreement_components, key=lambda key: (disagreement_components[key], key))
                if disagreement_components else None
            ),
            notableDecisionEvents=notable,
            phaseSummaries=self._phase_summaries(timeline, end),
        )

    def privacy_safe_jsonl(self, experiment_id: str) -> str:
        if self.get(experiment_id).ewps_model_version == "0.1.0":
            return self._privacy_safe_jsonl_v1(experiment_id)
        return self._privacy_safe_jsonl_v2(experiment_id)

    def _privacy_safe_jsonl_v1(self, experiment_id: str) -> str:
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

    def _privacy_safe_jsonl_v2(self, experiment_id: str) -> str:
        timeline = self.timeline(experiment_id)
        if not isinstance(timeline, V2ExperimentTimeline):
            raise ValueError("A v0.2 export requires a v0.2 timeline.")
        if timeline.session.release_id == EWPS_V2_RELEASE_ID:
            return self._privacy_safe_jsonl_v4(timeline)
        config = timeline.session.config
        snapshots = {item.path_id: item for item in timeline.session.candidate_snapshot}
        lines: list[str] = []
        for point in timeline.decisions:
            preferred = point.hysteresis.preferred_path_id
            for calculation in point.calculations:
                record = {
                    "schema_version": 3 if point.cadence is not None else 2,
                    "timestamp": point.timestamp.isoformat(),
                    "experiment_id": experiment_id,
                    "ewps_version": calculation.model_version,
                    "path_id": calculation.path_id,
                    "source_mode": timeline.session.source_mode,
                    "candidate_provenance": (
                        snapshots[calculation.path_id].model_dump(by_alias=False, mode="json")
                        if calculation.path_id in snapshots else None
                    ),
                    "lab_instance_id": timeline.session.lab_instance_id,
                    "lab_topology_version": timeline.session.lab_topology_version,
                    "initial_verification_status": timeline.session.initial_verification_status,
                    "controlled_impairment_scenario": timeline.session.controlled_impairment_scenario,
                    "workload_label": timeline.session.workload_label,
                    "cadence": (
                        point.cadence.model_dump(by_alias=False, mode="json")
                        if point.cadence is not None else None
                    ),
                    "raw": calculation.raw.model_dump(by_alias=False, mode="json"),
                    "evidence": calculation.evidence.model_dump(by_alias=False, mode="json"),
                    "confidence": calculation.confidence.model_dump(by_alias=False, mode="json"),
                    "model": {
                        "lambda": config.lambda_decay,
                        "k": config.density_k,
                        "alpha": config.alpha,
                        "beta": config.beta,
                        "p_perf_min": config.p_perf_min,
                        "weights": config.weights.model_dump(),
                        "loss_window_probes": config.loss_window_probes,
                        "hysteresis": config.hysteresis.model_dump(),
                    },
                    "cost": {"raw": calculation.raw_cost, "ewps": calculation.ewps_cost},
                    "eligibility": {
                        "eligible": calculation.eligible,
                        "state": calculation.eligibility_state,
                        "reasons": calculation.reasons,
                    },
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

    def _privacy_safe_jsonl_v4(self, timeline: V2ExperimentTimeline) -> str:
        config = timeline.session.config
        snapshots = {item.path_id: item for item in timeline.session.candidate_snapshot}
        records: list[tuple[datetime, int, dict]] = []
        for event in timeline.events:
            records.append((event.timestamp, 0, {
                "schema_version": 4,
                "record_type": "phase_event",
                "timestamp": event.timestamp.isoformat(),
                "experiment_id": timeline.session.experiment_id,
                "event": event.model_dump(by_alias=False, mode="json"),
            }))
        for point in timeline.decisions:
            preferred = point.hysteresis.preferred_path_id
            for calculation in point.calculations:
                scenario = point.scenario_phase.model_dump(by_alias=False, mode="json") if point.scenario_phase else None
                if scenario is not None:
                    profile = point.scenario_phase.path_profiles.get(calculation.path_id)
                    scenario["profile_id"] = (
                        profile.applied_profile_id or profile.requested_profile_id if profile else None
                    )
                records.append((point.timestamp, 1, {
                    "schema_version": 4,
                    "record_type": "measurement",
                    "timestamp": point.timestamp.isoformat(),
                    "experiment_id": timeline.session.experiment_id,
                    "ewps_version": calculation.model_version,
                    "path_id": calculation.path_id,
                    "source_mode": timeline.session.source_mode,
                    "candidate_provenance": (
                        snapshots[calculation.path_id].model_dump(by_alias=False, mode="json")
                        if calculation.path_id in snapshots else None
                    ),
                    "lab_instance_id": timeline.session.lab_instance_id,
                    "lab_topology_version": timeline.session.lab_topology_version,
                    "initial_verification_status": timeline.session.initial_verification_status,
                    "controlled_impairment_scenario": timeline.session.controlled_impairment_scenario,
                    "workload_label": timeline.session.workload_label,
                    "scenario": scenario,
                    "cadence": point.cadence.model_dump(by_alias=False, mode="json") if point.cadence else None,
                    "raw": calculation.raw.model_dump(by_alias=False, mode="json"),
                    "evidence": calculation.evidence.model_dump(by_alias=False, mode="json"),
                    "confidence": calculation.confidence.model_dump(by_alias=False, mode="json"),
                    "model": {
                        "lambda": config.lambda_decay,
                        "k": config.density_k,
                        "alpha": config.alpha,
                        "beta": config.beta,
                        "p_perf_min": config.p_perf_min,
                        "weights": config.weights.model_dump(),
                        "loss_window_probes": config.loss_window_probes,
                        "hysteresis": config.hysteresis.model_dump(),
                    },
                    "cost": {"raw": calculation.raw_cost, "ewps": calculation.ewps_cost},
                    "eligibility": {
                        "eligible": calculation.eligible,
                        "state": calculation.eligibility_state,
                        "reasons": calculation.reasons,
                    },
                    "algorithms": [item.model_dump(by_alias=False) for item in point.algorithms],
                    "decision": {
                        "preferred": calculation.path_id == preferred,
                        "would_switch": point.hysteresis.would_switch,
                        "reason": point.hysteresis.reason,
                        "switch_blocked_by": point.hysteresis.switch_blocked_by,
                    },
                }))
        summary = self._summary_v2(timeline.session.experiment_id)
        for phase in summary.phase_summaries:
            records.append((phase.ended_at, 2, {
                "schema_version": 4,
                "record_type": "phase_summary",
                "timestamp": phase.ended_at.isoformat(),
                "experiment_id": timeline.session.experiment_id,
                "phase_summary": phase.model_dump(by_alias=False, mode="json"),
            }))
        records.sort(key=lambda item: (item[0], item[1]))
        return "\n".join(_json(record) for _timestamp, _rank, record in records) + ("\n" if records else "")

    def privacy_safe_csv(self, experiment_id: str) -> str:
        if self.get(experiment_id).ewps_model_version == "0.1.0":
            return self._privacy_safe_csv_v1(experiment_id)
        return self._privacy_safe_csv_v2(experiment_id)

    def _privacy_safe_csv_v1(self, experiment_id: str) -> str:
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

    def _privacy_safe_csv_v2(self, experiment_id: str) -> str:
        timeline = self.timeline(experiment_id)
        if not isinstance(timeline, V2ExperimentTimeline):
            raise ValueError("A v0.2 export requires a v0.2 timeline.")
        if timeline.session.release_id == EWPS_V2_RELEASE_ID:
            return self._privacy_safe_csv_v4(timeline)
        output = io.StringIO(newline="")
        fields = [
            "schema_version", "timestamp", "experiment_id", "source_mode", "path_id",
            "candidate_label", "candidate_source_kind", "lab_instance_id",
            "lab_topology_version", "initial_verification_status", "controlled_impairment_scenario",
            "configured_interval_seconds", "cycle_started_at", "cycle_completed_at",
            "cycle_collection_duration_ms", "actual_start_to_start_seconds",
            "cadence_overrun_count",
            "instant_latency_ms", "rolling_latency_ms", "instant_jitter_ms",
            "rolling_jitter_ms", "instant_loss_pct", "rolling_loss_pct",
            "loss_sample_count", "probe_outcomes", "candidate_lifecycle",
            "telemetry_state", "age_seconds", "collection_started_at",
            "observation_validated_at", "collection_duration_ms", "mean_ms",
            "stddev_ms", "effective_samples", "freshness", "stability", "density",
            "performance_confidence", "topology_confidence", "topology_penalty",
            "raw_cost", "ewps_cost", "eligible", "eligibility_state", "ewps_preferred",
            "hysteresis_preferred", "switch_suppressed", "switch_blocked_by",
        ]
        writer = csv.DictWriter(output, fieldnames=fields)
        writer.writeheader()
        snapshots = {item.path_id: item for item in timeline.session.candidate_snapshot}
        for point in timeline.decisions:
            ewps = next((item.path_id for item in point.algorithms if item.algorithm == "ewps"), None)
            for item in point.calculations:
                snapshot = snapshots.get(item.path_id)
                writer.writerow({
                    "schema_version": 3 if point.cadence is not None else 2,
                    "timestamp": point.timestamp.isoformat(),
                    "experiment_id": experiment_id,
                    "source_mode": timeline.session.source_mode,
                    "path_id": item.path_id,
                    "candidate_label": snapshot.display_label if snapshot else "",
                    "candidate_source_kind": snapshot.source_kind if snapshot else "",
                    "lab_instance_id": timeline.session.lab_instance_id,
                    "lab_topology_version": timeline.session.lab_topology_version,
                    "initial_verification_status": timeline.session.initial_verification_status,
                    "controlled_impairment_scenario": timeline.session.controlled_impairment_scenario,
                    "configured_interval_seconds": (
                        point.cadence.configured_interval_seconds if point.cadence else ""
                    ),
                    "cycle_started_at": point.cadence.cycle_started_at if point.cadence else "",
                    "cycle_completed_at": point.cadence.cycle_completed_at if point.cadence else "",
                    "cycle_collection_duration_ms": (
                        point.cadence.collection_duration_ms if point.cadence else ""
                    ),
                    "actual_start_to_start_seconds": (
                        point.cadence.actual_start_to_start_seconds if point.cadence else ""
                    ),
                    "cadence_overrun_count": (
                        point.cadence.cadence_overrun_count if point.cadence else ""
                    ),
                    "instant_latency_ms": item.raw.latency_ms,
                    "rolling_latency_ms": item.raw.rolling_latency_ms,
                    "instant_jitter_ms": item.raw.jitter_ms,
                    "rolling_jitter_ms": item.raw.rolling_jitter_ms,
                    "instant_loss_pct": item.raw.loss_pct,
                    "rolling_loss_pct": item.raw.rolling_loss_pct,
                    "loss_sample_count": item.raw.loss_sample_count,
                    "probe_outcomes": "".join("1" if value else "0" for value in item.raw.probe_outcomes),
                    "candidate_lifecycle": item.raw.candidate_lifecycle,
                    "telemetry_state": item.raw.telemetry_state,
                    "age_seconds": item.evidence.age_seconds,
                    "collection_started_at": item.evidence.collection_started_at,
                    "observation_validated_at": item.evidence.observation_validated_at,
                    "collection_duration_ms": item.evidence.collection_duration_ms,
                    "mean_ms": item.evidence.mean_ms,
                    "stddev_ms": item.evidence.stddev_ms,
                    "effective_samples": item.evidence.effective_samples,
                    "freshness": item.confidence.freshness,
                    "stability": item.confidence.stability,
                    "density": item.confidence.density,
                    "performance_confidence": item.confidence.performance,
                    "topology_confidence": item.confidence.topology,
                    "topology_penalty": item.confidence.topology_penalty,
                    "raw_cost": item.raw_cost,
                    "ewps_cost": item.ewps_cost,
                    "eligible": item.eligible,
                    "eligibility_state": item.eligibility_state,
                    "ewps_preferred": item.path_id == ewps,
                    "hysteresis_preferred": item.path_id == point.hysteresis.preferred_path_id,
                    "switch_suppressed": point.hysteresis.suppressed,
                    "switch_blocked_by": point.hysteresis.switch_blocked_by,
                })
        return output.getvalue()

    def _privacy_safe_csv_v4(self, timeline: V2ExperimentTimeline) -> str:
        """Schema-v4 CSV uses typed rows so immutable events remain first-class."""

        output = io.StringIO(newline="")
        fields = [
            "schema_version", "record_type", "timestamp", "experiment_id", "path_id",
            "scenario_id", "phase_index", "phase_id", "profile_id", "path_profiles_json",
            "event_type", "application_succeeded", "verification", "affected_path_ids",
            "instant_latency_ms", "rolling_latency_ms", "instant_jitter_ms",
            "rolling_jitter_ms", "instant_loss_pct", "rolling_loss_pct",
            "performance_confidence", "raw_cost", "ewps_cost", "eligible",
            "telemetry_state", "configured_interval_seconds", "actual_start_to_start_seconds",
            "cadence_overrun_count", "phase_summary_json",
        ]
        writer = csv.DictWriter(output, fieldnames=fields)
        writer.writeheader()
        rows: list[tuple[datetime, int, dict]] = []
        for event in timeline.events:
            rows.append((event.timestamp, 0, {
                "schema_version": 4,
                "record_type": "phase_event",
                "timestamp": event.timestamp.isoformat(),
                "experiment_id": event.experiment_id,
                "scenario_id": event.scenario_id,
                "phase_index": event.new_phase_index,
                "phase_id": event.new_phase_id,
                "path_profiles_json": _json({
                    path_id: profile.model_dump(by_alias=False, mode="json")
                    for path_id, profile in event.path_profiles.items()
                }),
                "event_type": event.event_type,
                "application_succeeded": event.application_succeeded,
                "verification": event.verification,
                "affected_path_ids": ",".join(event.affected_path_ids),
            }))
        for point in timeline.decisions:
            for item in point.calculations:
                phase = point.scenario_phase
                path_profile = phase.path_profiles.get(item.path_id) if phase else None
                rows.append((point.timestamp, 1, {
                    "schema_version": 4,
                    "record_type": "measurement",
                    "timestamp": point.timestamp.isoformat(),
                    "experiment_id": timeline.session.experiment_id,
                    "path_id": item.path_id,
                    "scenario_id": phase.scenario_id if phase else "",
                    "phase_index": phase.phase_index if phase else "",
                    "phase_id": phase.phase_id if phase else "",
                    "profile_id": (
                        path_profile.applied_profile_id or path_profile.requested_profile_id
                        if path_profile else ""
                    ),
                    "path_profiles_json": _json({
                        path_id: profile.model_dump(by_alias=False, mode="json")
                        for path_id, profile in phase.path_profiles.items()
                    }) if phase else "",
                    "instant_latency_ms": item.raw.latency_ms,
                    "rolling_latency_ms": item.raw.rolling_latency_ms,
                    "instant_jitter_ms": item.raw.jitter_ms,
                    "rolling_jitter_ms": item.raw.rolling_jitter_ms,
                    "instant_loss_pct": item.raw.loss_pct,
                    "rolling_loss_pct": item.raw.rolling_loss_pct,
                    "performance_confidence": item.confidence.performance,
                    "raw_cost": item.raw_cost,
                    "ewps_cost": item.ewps_cost,
                    "eligible": item.eligible,
                    "telemetry_state": item.raw.telemetry_state,
                    "configured_interval_seconds": (
                        point.cadence.configured_interval_seconds if point.cadence else ""
                    ),
                    "actual_start_to_start_seconds": (
                        point.cadence.actual_start_to_start_seconds if point.cadence else ""
                    ),
                    "cadence_overrun_count": (
                        point.cadence.cadence_overrun_count if point.cadence else ""
                    ),
                }))
        summary = self._summary_v2(timeline.session.experiment_id)
        for phase in summary.phase_summaries:
            rows.append((phase.ended_at, 2, {
                "schema_version": 4,
                "record_type": "phase_summary",
                "timestamp": phase.ended_at.isoformat(),
                "experiment_id": timeline.session.experiment_id,
                "scenario_id": phase.scenario_id,
                "phase_index": phase.phase_index,
                "phase_id": phase.phase_id,
                "phase_summary_json": _json(phase),
            }))
        rows.sort(key=lambda item: (item[0], item[1]))
        for _timestamp, _rank, row in rows:
            writer.writerow(row)
        return output.getvalue()


_store: EWPSResearchStore | None = None


def get_ewps_store() -> EWPSResearchStore:
    global _store
    expected = get_settings().data_dir / "ewps-research.sqlite3"
    if _store is None or _store.path != expected:
        _store = EWPSResearchStore(expected)
    return _store
