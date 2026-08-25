"""EWPS v0.2 lifecycle, telemetry, replay, export, and lab orchestration."""
from __future__ import annotations

from collections import defaultdict, deque
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import statistics
import threading
import time
from typing import Deque
import uuid

from .config import get_settings
from .ewps_engine import EWPSDecisionEngine
from .ewps_lab import LAB_PATHS, LAB_TOPOLOGY_VERSION, EWPSLabManager, get_ewps_lab
from .ewps_models import EWPSConfig, ExperimentSession, ReplayResult, RawMetrics
from .ewps_service import EWPSService as LegacyEWPSService
from .ewps_store import EWPSResearchStore, get_ewps_store
from .ewps_telemetry import InternalCandidate, ProbeResult, candidate_catalog, measure_candidate
from .ewps_v2_engine import EWPSV2DecisionEngine
from .ewps_v2_models import (
    EWPSV2Config,
    ExportSaveResult,
    LabProfileName,
    LabScenarioAdvanceResponse,
    LabScenarioName,
    LabStatus,
    V2CandidatePath,
    V2CandidateSnapshot,
    V2CadenceObservation,
    V2DecisionPoint,
    V2EvidenceInput,
    V2ExperimentCreateRequest,
    V2ExperimentEvent,
    V2ExperimentSession,
    V2ExperimentSummary,
    V2ExperimentTimeline,
    V2RawMetrics,
    V2ReplayResult,
    V2ScenarioPhaseSnapshot,
)
from .live_state import get_live_state


@dataclass(frozen=True)
class V2InternalCandidate:
    public: V2CandidatePath
    source_ip: str | None = None
    legacy: InternalCandidate | None = None


class V2PathRuntime:
    def __init__(self, config: EWPSV2Config) -> None:
        self.latencies: Deque[float] = deque(maxlen=config.rolling_window)
        self.loss_outcomes: Deque[bool] = deque(maxlen=config.loss_window_probes)
        self.last_validated_at: datetime | None = None
        self.lifecycle = "PROBING"
        self.consecutive_failures = 0
        self.ever_viable = False
        self.recovering_successes = 0


class V2EngineRuntime:
    def __init__(self, config: EWPSV2Config) -> None:
        self.engine = EWPSV2DecisionEngine(config)
        self.paths: dict[str, V2PathRuntime] = defaultdict(lambda: V2PathRuntime(config))


class EWPSV2Service:
    def __init__(
        self,
        store: EWPSResearchStore | None = None,
        lab: EWPSLabManager | None = None,
    ) -> None:
        self.store = store or get_ewps_store()
        self.lab = lab or get_ewps_lab()
        self._lock = threading.RLock()
        self._cycle_lock = threading.RLock()
        self._active_id: str | None = None
        self._runtime: V2EngineRuntime | None = None
        self._candidates: dict[str, V2InternalCandidate] = {}
        self._known_lifecycles: dict[str, str] = {}
        self._stop = threading.Event()
        self._run = threading.Event()
        self._scheduler_wake = threading.Event()
        self._thread: threading.Thread | None = None
        self._lab_lost_recorded = False
        self._previous_cycle_started_monotonic: float | None = None
        self._cadence_overrun_count = 0
        self._monotonic = time.monotonic
        self._utcnow = lambda: datetime.now(timezone.utc)
        for recorded in self.store.list(500):
            if recorded.status == "RUNNING":
                recovered = self.store.transition(recorded.experiment_id, "PAUSED")
                if recovered.ewps_model_version == "0.2.0" and self._active_id is None:
                    self._active_id = recovered.experiment_id

    def _catalog(self) -> list[V2InternalCandidate]:
        candidates: list[V2InternalCandidate] = []
        for item in candidate_catalog():
            path_id = item.public.path_id
            lifecycle = self._known_lifecycles.get(path_id, "PROBING")
            candidates.append(V2InternalCandidate(
                public=V2CandidatePath(
                    pathId=path_id,
                    displayLabel=item.public.display_label,
                    adapterName=item.public.adapter_name,
                    sourceKind="real_interface",
                    lifecycle=lifecycle,
                    topologyEvidence=item.public.topology_evidence,
                    topologyDetail=item.public.topology_detail,
                    reachable=item.public.reachable,
                    eligibleForLiveMeasurement=item.public.eligible_for_live_measurement,
                ),
                source_ip=item.source_ip,
                legacy=item,
            ))
        for item in self.lab.candidates():
            lifecycle = self._known_lifecycles.get(item.path_id, item.lifecycle)
            candidates.append(V2InternalCandidate(public=item.model_copy(update={"lifecycle": lifecycle})))
        real_count = sum(item.public.source_kind == "real_interface" for item in candidates)
        lab_index = 0
        for item in candidates:
            if item.public.source_kind == "controlled_lab":
                item.public.display_label = f"Path {chr(65 + lab_index)}"
                lab_index += 1
            elif real_count > 0:
                item.public.display_label = f"Real {item.public.display_label}"
        return candidates

    def candidates(self) -> list[V2CandidatePath]:
        return [item.public for item in self._catalog()]

    @staticmethod
    def _snapshot(candidate: V2InternalCandidate) -> V2CandidateSnapshot:
        public = candidate.public
        return V2CandidateSnapshot(
            pathId=public.path_id,
            displayLabel=public.display_label,
            adapterName=public.adapter_name,
            sourceKind=public.source_kind,
            topologyEvidence=public.topology_evidence,
            topologyDetail=public.topology_detail,
            diversityClaim=public.diversity_claim,
        )

    @staticmethod
    def _public_from_snapshot(snapshot: V2CandidateSnapshot) -> V2CandidatePath:
        return V2CandidatePath(
            pathId=snapshot.path_id,
            displayLabel=snapshot.display_label,
            adapterName=snapshot.adapter_name,
            sourceKind=snapshot.source_kind,
            lifecycle="PROBING",
            topologyEvidence=snapshot.topology_evidence,
            topologyDetail=snapshot.topology_detail,
            diversityClaim=snapshot.diversity_claim,
        )

    def _creation_binding(self, request: V2ExperimentCreateRequest):
        selected_ids = list(request.candidate_path_ids)
        if request.source_mode == "CONTROLLED_DUAL_PATH":
            expected = list(LAB_PATHS)
            if selected_ids != expected:
                raise ValueError("CONTROLLED_DUAL_PATH requires exactly lab-path-a and lab-path-b in stable order.")
            if request.controlled_scenario is None:
                raise ValueError("CONTROLLED_DUAL_PATH requires an explicitly prepared controlled scenario.")
            status = self.lab.validate_for_experiment(request.controlled_scenario)
            if not status.prerequisites_passed:
                raise ValueError("Controlled experiment startup requires passing WSL2 prerequisites.")
            if not status.ready or not all(path.independently_validated for path in status.paths):
                raise ValueError("Controlled experiment startup requires two freshly verified telemetry paths.")
            initial_phase = self.lab.current_phase_snapshot()
            if initial_phase is None or initial_phase.phase_index != 0:
                raise ValueError("Reset the controlled scenario to its authoritative initial phase before creating an experiment.")
            catalog = {
                item.public.path_id: item
                for item in self._catalog()
                if item.public.source_kind == "controlled_lab"
            }
            if list(catalog) != expected:
                raise ValueError("The verified contained lab did not produce exactly two controlled candidates.")
            selected = [catalog[path_id] for path_id in expected]
            return (
                selected,
                status.lab_instance_id,
                status.topology_version,
                "VERIFIED",
                status.scenario_id,
                initial_phase,
            )

        if request.controlled_scenario is not None:
            raise ValueError("REAL_INTERFACES cannot reference a controlled-lab scenario.")
        catalog = {
            item.public.path_id: item
            for item in self._catalog()
            if item.public.source_kind == "real_interface"
        }
        missing = [path_id for path_id in selected_ids if path_id not in catalog]
        if missing:
            raise ValueError("REAL_INTERFACES can contain only currently discovered real-interface candidates.")
        return ([catalog[path_id] for path_id in selected_ids], None, None, "NOT_APPLICABLE", None, None)

    def create(self, request: V2ExperimentCreateRequest) -> V2ExperimentSession:
        (
            selected,
            lab_instance_id,
            topology_version,
            verification,
            scenario,
            initial_scenario_phase,
        ) = self._creation_binding(request)
        with self._lock:
            if self._active_id:
                active = self.store.get(self._active_id)
                if active.status in {"CREATED", "RUNNING", "PAUSED"}:
                    raise ValueError("Stop the active EWPS experiment before creating another.")
            session = self.store.create_v2(
                request,
                candidate_snapshot=[self._snapshot(item) for item in selected],
                lab_instance_id=lab_instance_id,
                lab_topology_version=topology_version,
                initial_verification_status=verification,
                controlled_scenario=scenario,
                initial_scenario_phase=initial_scenario_phase,
            )
            self._active_id = session.experiment_id
            return session

    def list(self, limit: int = 50):
        return self.store.list(limit)

    def get(self, experiment_id: str):
        return self.store.get(experiment_id)

    def current(self):
        with self._lock:
            if self._active_id:
                try:
                    session = self.store.get(self._active_id)
                    if session.status != "COMPLETED":
                        return session
                except KeyError:
                    pass
                self._active_id = None
            for session in self.store.list(500):
                if (
                    session.status in {"CREATED", "RUNNING", "PAUSED"}
                    and session.ewps_model_version == "0.2.0"
                ):
                    self._active_id = session.experiment_id
                    return session
            return None

    def _resolve_candidates(self, session: V2ExperimentSession) -> dict[str, V2InternalCandidate]:
        if session.source_mode == "LEGACY_UNBOUND":
            raise ValueError("Legacy v0.2.0 sessions without source provenance are replay-only.")
        snapshots = {item.path_id: item for item in session.candidate_snapshot}
        if list(session.candidate_path_ids) != [item.path_id for item in session.candidate_snapshot]:
            raise ValueError("The immutable candidate snapshot does not match the recorded candidate IDs.")
        if session.source_mode == "CONTROLLED_DUAL_PATH":
            if session.candidate_path_ids != list(LAB_PATHS):
                raise ValueError("The controlled experiment does not contain the exact controlled dual-path identity set.")
            if any(item.source_kind != "controlled_lab" for item in session.candidate_snapshot):
                raise ValueError("The controlled experiment contains contradictory candidate provenance.")
            status = self.lab.validate_for_experiment(session.controlled_impairment_scenario)
            if not status.ready or not status.prerequisites_passed:
                raise ValueError("The controlled lab must pass fresh two-path verification before start or resume.")
            if (
                status.lab_instance_id != session.lab_instance_id
                or status.topology_version != session.lab_topology_version
                or session.lab_topology_version != LAB_TOPOLOGY_VERSION
            ):
                raise ValueError("The current controlled lab is not the immutable lab instance recorded by this experiment.")
            actual_phase = self.lab.current_phase_snapshot()
            recorded = self.store.timeline(session.experiment_id)
            successful_events = [event for event in recorded.events if event.application_succeeded]
            expected_index = (
                successful_events[-1].new_phase_index
                if successful_events else (
                    session.initial_scenario_phase.phase_index
                    if session.initial_scenario_phase is not None else None
                )
            )
            if actual_phase is None or actual_phase.phase_index != expected_index:
                raise ValueError("The controlled lab phase does not match the experiment's immutable phase timeline.")
            return {
                path_id: V2InternalCandidate(public=self._public_from_snapshot(snapshots[path_id]))
                for path_id in session.candidate_path_ids
            }

        if session.source_mode != "REAL_INTERFACES":
            raise ValueError("Simulator sessions cannot enter the live collector.")
        if any(item.source_kind != "real_interface" for item in session.candidate_snapshot):
            raise ValueError("The real-interface experiment contains contradictory candidate provenance.")
        discovered = {
            item.public.path_id: item
            for item in self._catalog()
            if item.public.source_kind == "real_interface"
        }
        missing = [path_id for path_id in session.candidate_path_ids if path_id not in discovered]
        if missing:
            raise ValueError("A recorded real-interface binding is no longer available.")
        return {
            path_id: V2InternalCandidate(
                public=self._public_from_snapshot(snapshots[path_id]),
                source_ip=discovered[path_id].source_ip,
                legacy=discovered[path_id].legacy,
            )
            for path_id in session.candidate_path_ids
        }

    def _restore_runtime(self, session: V2ExperimentSession) -> V2EngineRuntime:
        runtime = V2EngineRuntime(session.config)
        timeline = self.store.timeline(session.experiment_id)
        if not isinstance(timeline, V2ExperimentTimeline):
            raise ValueError("Only v0.2 sessions can be resumed by the v0.2 collector.")
        for point in timeline.decisions:
            runtime.engine.evaluate(
                point.timestamp,
                [(item.path_id, item.raw, item.evidence) for item in point.calculations],
            )
            for item in point.calculations:
                state = runtime.paths[item.path_id]
                if item.evidence.observation_validated_at and item.raw.latency_ms is not None:
                    state.latencies.append(item.raw.latency_ms)
                    state.loss_outcomes.extend(item.raw.probe_outcomes)
                    state.last_validated_at = item.evidence.observation_validated_at
                state.lifecycle = item.raw.candidate_lifecycle
                state.ever_viable = state.ever_viable or state.lifecycle in {"VIABLE", "RECOVERING"}
                self._known_lifecycles[item.path_id] = state.lifecycle
        return runtime

    def start(self, experiment_id: str) -> V2ExperimentSession:
        with self._lock:
            session = self.store.get(experiment_id)
            if not isinstance(session, V2ExperimentSession):
                raise ValueError("EWPS v0.1 sessions are immutable replay-only evidence in this release.")
            if session.status not in {"CREATED", "PAUSED"}:
                raise ValueError("Only a created or paused experiment can be started.")
            if self._active_id and self._active_id != experiment_id:
                active = self.store.get(self._active_id)
                if active.status in {"CREATED", "RUNNING", "PAUSED"}:
                    raise ValueError("Another EWPS experiment is active.")
            self._candidates = self._resolve_candidates(session)
            self._lab_lost_recorded = False
            if self._runtime is None or self._active_id != experiment_id:
                self._runtime = self._restore_runtime(session)
            self._active_id = experiment_id
            transitioned = self.store.transition(experiment_id, "RUNNING")
            if not isinstance(transitioned, V2ExperimentSession):
                raise RuntimeError("The v0.2 session changed model version unexpectedly.")
            self._stop.clear()
            self._run.set()
            self._previous_cycle_started_monotonic = None
            timeline = self.store.timeline(experiment_id)
            self._cadence_overrun_count = max(
                (
                    point.cadence.cadence_overrun_count
                    for point in timeline.decisions
                    if isinstance(point, V2DecisionPoint) and point.cadence is not None
                ),
                default=0,
            )
            self._scheduler_wake.set()
            if self._thread is None or not self._thread.is_alive():
                self._thread = threading.Thread(
                    target=self._collector_loop,
                    name="switchops-ewps-v02-shadow-collector",
                    daemon=True,
                )
                self._thread.start()
            return transitioned

    def pause(self, experiment_id: str):
        with self._lock:
            if experiment_id != self._active_id:
                raise ValueError("This is not the active EWPS experiment.")
            self._run.clear()
            self._previous_cycle_started_monotonic = None
            self._scheduler_wake.set()
            return self.store.transition(experiment_id, "PAUSED")

    def stop(self, experiment_id: str):
        with self._lock:
            session = self.store.get(experiment_id)
            if session.status not in {"CREATED", "RUNNING", "PAUSED"}:
                raise ValueError("This EWPS experiment is already complete.")
            self._run.clear()
            self._stop.set()
            self._scheduler_wake.set()
            completed = self.store.transition(experiment_id, "COMPLETED")
            thread = self._thread
        if thread and thread is not threading.current_thread():
            thread.join(timeout=8.0)
        with self._lock:
            self._thread = None
            self._runtime = None
            self._candidates = {}
            self._active_id = None
        return completed, self.store.summary(experiment_id)

    @staticmethod
    def _seconds_until_next_cycle(
        configured_interval_seconds: float,
        cycle_started_monotonic: float,
        ready_for_next_cycle_monotonic: float,
    ) -> float:
        """Return the remaining start-to-start delay without allowing overlap."""

        elapsed = max(0.0, ready_for_next_cycle_monotonic - cycle_started_monotonic)
        return max(0.0, configured_interval_seconds - elapsed)

    def _collector_loop(self) -> None:
        while not self._stop.is_set():
            if not self._run.wait(timeout=0.5):
                continue
            with self._cycle_lock:
                with self._lock:
                    if not self._active_id:
                        break
                    session = self.store.get(self._active_id)
                    interval = session.config.sample_interval_seconds if isinstance(session, V2ExperimentSession) else 5.0
                    cycle_started_monotonic = self._monotonic()
                    cycle_started_at = self._utcnow()
                    actual_start_to_start = (
                        None
                        if self._previous_cycle_started_monotonic is None
                        else max(0.0, cycle_started_monotonic - self._previous_cycle_started_monotonic)
                    )
                    self._previous_cycle_started_monotonic = cycle_started_monotonic
                try:
                    self.sample_once(
                        cycle_started_at=cycle_started_at,
                        cycle_started_monotonic=cycle_started_monotonic,
                        actual_start_to_start_seconds=actual_start_to_start,
                    )
                except Exception:
                    pass
            remaining = self._seconds_until_next_cycle(
                interval,
                cycle_started_monotonic,
                self._monotonic(),
            )
            if remaining <= 0 or self._stop.is_set() or not self._run.is_set():
                continue
            self._scheduler_wake.clear()
            if self._stop.is_set() or not self._run.is_set():
                continue
            self._scheduler_wake.wait(timeout=remaining)

    def _measure(self, candidate: V2InternalCandidate, count: int) -> ProbeResult:
        if candidate.public.source_kind == "controlled_lab":
            return self.lab.measure(candidate.public.path_id, count)
        if candidate.legacy is None:
            raise ValueError("The real-interface candidate lost its private source binding.")
        return measure_candidate(candidate.legacy, count)

    @staticmethod
    def _deferred_result(path_id: str) -> ProbeResult:
        now = datetime.now(timezone.utc)
        return ProbeResult(
            path_id=path_id,
            observed_at=now,
            raw=RawMetrics(sampleCount=0, reachable=False),
            collection_started_at=now,
            observation_validated_at=None,
            collection_duration_ms=0.0,
            probe_outcomes=(),
            failure_reason="candidate_reprobe_deferred",
        )

    @staticmethod
    def _controlled_lab_lost_result(path_id: str) -> ProbeResult:
        now = datetime.now(timezone.utc)
        return ProbeResult(
            path_id=path_id,
            observed_at=now,
            raw=RawMetrics(sampleCount=0, reachable=False),
            collection_started_at=now,
            observation_validated_at=None,
            collection_duration_ms=0.0,
            probe_outcomes=(),
            failure_reason="controlled_lab_lost",
        )

    def _recorded_phase_snapshot(self, session: V2ExperimentSession) -> V2ScenarioPhaseSnapshot | None:
        timeline = self.store.timeline(session.experiment_id)
        if not isinstance(timeline, V2ExperimentTimeline):
            return None
        successful = [event for event in timeline.events if event.application_succeeded]
        if not successful:
            return session.initial_scenario_phase
        event = successful[-1]
        return V2ScenarioPhaseSnapshot(
            scenarioId=event.scenario_id,
            phaseIndex=event.new_phase_index,
            phaseId=event.new_phase_id,
            labInstanceId=event.lab_instance_id,
            pathProfiles=event.path_profiles,
        )

    def sample_once(
        self,
        *,
        cycle_started_at: datetime | None = None,
        cycle_started_monotonic: float | None = None,
        actual_start_to_start_seconds: float | None = None,
    ) -> None:
        # A phase transition may occur only between complete probe cycles.
        with self._cycle_lock:
            self._sample_once_locked(
                cycle_started_at=cycle_started_at,
                cycle_started_monotonic=cycle_started_monotonic,
                actual_start_to_start_seconds=actual_start_to_start_seconds,
            )

    def _sample_once_locked(
        self,
        *,
        cycle_started_at: datetime | None = None,
        cycle_started_monotonic: float | None = None,
        actual_start_to_start_seconds: float | None = None,
    ) -> None:
        with self._lock:
            experiment_id = self._active_id
            runtime = self._runtime
            candidates = list(self._candidates.values())
        if not experiment_id or runtime is None:
            raise ValueError("No EWPS v0.2 experiment is active.")
        session = self.store.get(experiment_id)
        if not isinstance(session, V2ExperimentSession) or session.status != "RUNNING":
            return
        controlled_lab_lost = bool(
            session.source_mode == "CONTROLLED_DUAL_PATH"
            and not self.lab.binding_is_current(session.lab_instance_id, session.lab_topology_version)
        )
        phase_snapshot: V2ScenarioPhaseSnapshot | None = None
        if session.source_mode == "CONTROLLED_DUAL_PATH":
            phase_snapshot = (
                self._recorded_phase_snapshot(session)
                if controlled_lab_lost else self.lab.current_phase_snapshot()
            )
            if phase_snapshot is None or phase_snapshot.lab_instance_id != session.lab_instance_id:
                raise RuntimeError("The controlled phase snapshot does not match the experiment's immutable lab binding.")
        cycle = runtime.engine.decision_index
        results: dict[str, ProbeResult] = {}
        to_probe: list[V2InternalCandidate] = []
        for candidate in candidates:
            state = runtime.paths[candidate.public.path_id]
            if controlled_lab_lost:
                results[candidate.public.path_id] = self._controlled_lab_lost_result(candidate.public.path_id)
            elif (
                state.lifecycle == "PERSISTENTLY_UNAVAILABLE"
                and cycle % session.config.unavailable_reprobe_cycles != 0
            ):
                results[candidate.public.path_id] = self._deferred_result(candidate.public.path_id)
            else:
                to_probe.append(candidate)
        if to_probe:
            with ThreadPoolExecutor(max_workers=min(8, len(to_probe)), thread_name_prefix="ewps-probe") as pool:
                pending = {
                    pool.submit(self._measure, candidate, session.config.probe_count): candidate.public.path_id
                    for candidate in to_probe
                }
                for future in as_completed(pending):
                    path_id = pending[future]
                    try:
                        results[path_id] = future.result()
                    except Exception:
                        now = datetime.now(timezone.utc)
                        results[path_id] = ProbeResult(
                            path_id=path_id,
                            observed_at=now,
                            raw=RawMetrics(sampleCount=0, reachable=False),
                            collection_started_at=now,
                            collection_duration_ms=0.0,
                            failure_reason="probe_unavailable",
                        )
        cadence: V2CadenceObservation | None = None
        if cycle_started_at is not None and cycle_started_monotonic is not None:
            cycle_completed_at = self._utcnow()
            collection_duration_seconds = max(0.0, self._monotonic() - cycle_started_monotonic)
            with self._lock:
                if collection_duration_seconds > session.config.sample_interval_seconds:
                    self._cadence_overrun_count += 1
                cadence_overrun_count = self._cadence_overrun_count
            cadence = V2CadenceObservation(
                configuredIntervalSeconds=session.config.sample_interval_seconds,
                cycleStartedAt=cycle_started_at,
                cycleCompletedAt=cycle_completed_at,
                collectionDurationMs=collection_duration_seconds * 1000.0,
                actualStartToStartSeconds=actual_start_to_start_seconds,
                cadenceOverrunCount=cadence_overrun_count,
            )
        timestamp = max((item.observed_at for item in results.values()), default=datetime.now(timezone.utc))
        path_inputs: list[tuple[str, V2RawMetrics, V2EvidenceInput]] = []
        for candidate in candidates:
            path_id = candidate.public.path_id
            result = results[path_id]
            state = runtime.paths[path_id]
            old_lifecycle = state.lifecycle
            validated = result.observation_validated_at is not None and bool(result.probe_outcomes)
            stale_injection = result.failure_reason == "controlled_evidence_stale"
            deferred = result.failure_reason == "candidate_reprobe_deferred"
            lab_lost = result.failure_reason == "controlled_lab_lost"
            transient_failure = candidate_unavailable_event = recovery_event = False
            if validated:
                if result.raw.latency_ms is not None:
                    state.latencies.append(result.raw.latency_ms)
                state.loss_outcomes.extend(result.probe_outcomes)
                state.last_validated_at = result.observation_validated_at
                state.consecutive_failures = 0
                if old_lifecycle == "PERSISTENTLY_UNAVAILABLE":
                    state.lifecycle = "RECOVERING"
                    state.recovering_successes = 1
                    recovery_event = True
                elif old_lifecycle == "RECOVERING":
                    state.recovering_successes += 1
                    if state.recovering_successes >= 2:
                        state.lifecycle = "VIABLE"
                else:
                    state.lifecycle = "VIABLE"
                state.ever_viable = True
            elif stale_injection or deferred:
                pass
            elif lab_lost:
                state.consecutive_failures += 1
                state.lifecycle = "PERSISTENTLY_UNAVAILABLE"
                candidate_unavailable_event = old_lifecycle != "PERSISTENTLY_UNAVAILABLE"
            else:
                state.consecutive_failures += 1
                transient_failure = state.ever_viable and old_lifecycle in {"VIABLE", "RECOVERING"}
                if state.consecutive_failures >= session.config.unavailable_failure_threshold:
                    state.lifecycle = "PERSISTENTLY_UNAVAILABLE"
                    candidate_unavailable_event = old_lifecycle != "PERSISTENTLY_UNAVAILABLE"
                elif not state.ever_viable:
                    state.lifecycle = "PROBING"

            self._known_lifecycles[path_id] = state.lifecycle
            latencies = list(state.latencies)
            outcomes = list(state.loss_outcomes)
            rolling_latency = statistics.fmean(latencies) if latencies else None
            rolling_jitter = statistics.pstdev(latencies) if len(latencies) > 1 else (0.0 if latencies else None)
            rolling_loss = (
                (len(outcomes) - sum(outcomes)) / len(outcomes) * 100.0 if outcomes else None
            )
            if validated:
                reachable: bool | None = True
                telemetry_state = "validated"
            elif stale_injection:
                reachable = None
                telemetry_state = "evidence_stale"
            elif lab_lost:
                reachable = False
                telemetry_state = "controlled_lab_lost"
            elif deferred:
                reachable = None
                telemetry_state = "reprobe_deferred"
            elif result.failure_reason in {"no_parseable_replies", "complete_probe_failure"}:
                reachable = False
                telemetry_state = "transient_failure" if transient_failure else "candidate_unavailable"
            else:
                reachable = None
                telemetry_state = "transient_failure" if transient_failure else "candidate_unavailable"
            routing_usable = (
                rolling_latency is not None
                and rolling_jitter is not None
                and rolling_loss is not None
                and reachable is not False
                and state.lifecycle != "PERSISTENTLY_UNAVAILABLE"
            )
            current = candidate
            last_validated = state.last_validated_at
            age = max(0.0, (timestamp - last_validated).total_seconds()) if last_validated else None
            sent = result.raw.interface_packets_sent
            received_packets = result.raw.interface_packets_received
            errors = result.raw.interface_errors
            drops = result.raw.interface_drops
            raw = V2RawMetrics(
                latencyMs=result.raw.latency_ms,
                rollingLatencyMs=rolling_latency,
                jitterMs=result.raw.jitter_ms,
                rollingJitterMs=rolling_jitter,
                lossPct=result.raw.loss_pct,
                rollingLossPct=rolling_loss,
                sampleCount=result.raw.sample_count,
                lossSampleCount=len(outcomes),
                probeOutcomes=list(result.probe_outcomes),
                reachable=reachable,
                routingMetricsUsable=routing_usable,
                telemetryState=telemetry_state,
                candidateLifecycle=state.lifecycle,
                transientFailure=transient_failure,
                candidateUnavailableEvent=candidate_unavailable_event,
                recoveryEvent=recovery_event,
                interfacePacketsSent=sent,
                interfacePacketsReceived=received_packets,
                interfaceErrors=errors,
                interfaceDrops=drops,
            )
            evidence = V2EvidenceInput(
                ageSeconds=age,
                meanMs=rolling_latency,
                stddevMs=rolling_jitter,
                effectiveSamples=float(sum(outcomes)),
                topologyEvidence=current.public.topology_evidence,
                collectionStartedAt=result.collection_started_at,
                observationValidatedAt=result.observation_validated_at,
                collectionDurationMs=result.collection_duration_ms,
            )
            path_inputs.append((path_id, raw, evidence))
        point = runtime.engine.evaluate(timestamp, path_inputs)
        if cadence is not None:
            point = point.model_copy(update={"cadence": cadence})
        if phase_snapshot is not None:
            point = point.model_copy(update={"scenario_phase": phase_snapshot})
        if controlled_lab_lost and not self._lab_lost_recorded:
            point = point.model_copy(update={"events": [*point.events, "CONTROLLED_LAB_LOST"]})
            self._lab_lost_recorded = True
        self.store.append(experiment_id, point)
        get_live_state().hub.publish("ewps_decision", point.model_dump(by_alias=True, mode="json"))

    def timeline(self, experiment_id: str):
        return self.store.timeline(experiment_id)

    def summary(self, experiment_id: str):
        return self.store.summary(experiment_id)

    def replay(self, experiment_id: str, config: dict | EWPSConfig | EWPSV2Config | None = None):
        timeline = self.store.timeline(experiment_id)
        if isinstance(timeline, V2ExperimentTimeline):
            replay_config = (
                EWPSV2Config.model_validate(config)
                if isinstance(config, dict)
                else (config or timeline.session.config).model_copy(deep=True)
            )
            if not isinstance(replay_config, EWPSV2Config):
                raise ValueError("A v0.2 replay requires v0.2 parameters.")
            engine = EWPSV2DecisionEngine(replay_config)
            decisions = []
            for point in timeline.decisions:
                replayed = engine.evaluate(
                    point.timestamp,
                    [(item.path_id, item.raw, item.evidence) for item in point.calculations],
                )
                if point.cadence is not None:
                    replayed = replayed.model_copy(update={"cadence": point.cadence})
                if point.scenario_phase is not None:
                    replayed = replayed.model_copy(update={"scenario_phase": point.scenario_phase})
                decisions.append(replayed)
            canonical_decisions = []
            for item in decisions:
                payload = item.model_dump(by_alias=True, mode="json")
                # Cadence is instrumentation, not an EWPS engine input. Leaving
                # it out preserves every historical v0.2 deterministic digest.
                payload.pop("cadence", None)
                payload.pop("scenarioPhase", None)
                canonical_decisions.append(payload)
            canonical = json.dumps(
                canonical_decisions,
                sort_keys=True,
                separators=(",", ":"),
            )
            return V2ReplayResult(
                sourceExperimentId=experiment_id,
                sourceMode=timeline.session.source_mode,
                candidateSnapshot=timeline.session.candidate_snapshot,
                labInstanceId=timeline.session.lab_instance_id,
                labTopologyVersion=timeline.session.lab_topology_version,
                controlledImpairmentScenario=timeline.session.controlled_impairment_scenario,
                config=replay_config,
                deterministicDigest=hashlib.sha256(canonical.encode("utf-8")).hexdigest(),
                decisions=decisions,
                events=timeline.events,
            )
        replay_config = (
            EWPSConfig.model_validate(config)
            if isinstance(config, dict)
            else (config or timeline.session.config).model_copy(deep=True)
        )
        if not isinstance(replay_config, EWPSConfig):
            raise ValueError("A v0.1 replay requires v0.1 parameters.")
        engine = EWPSDecisionEngine(replay_config)
        decisions = [
            engine.evaluate(
                point.timestamp,
                [(item.path_id, item.raw, item.evidence) for item in point.calculations],
            )
            for point in timeline.decisions
        ]
        canonical = json.dumps(
            [item.model_dump(by_alias=True, mode="json") for item in decisions],
            sort_keys=True,
            separators=(",", ":"),
        )
        return ReplayResult(
            sourceExperimentId=experiment_id,
            config=replay_config,
            deterministicDigest=hashlib.sha256(canonical.encode("utf-8")).hexdigest(),
            decisions=decisions,
        )

    def export(self, experiment_id: str, format_name: str) -> tuple[str, str, Path]:
        if format_name == "jsonl":
            content = self.store.privacy_safe_jsonl(experiment_id)
            media_type = "application/x-ndjson"
        elif format_name == "csv":
            content = self.store.privacy_safe_csv(experiment_id)
            media_type = "text/csv"
        elif format_name == "json":
            summary = self.store.summary(experiment_id)
            export_timeline = self.store.timeline(experiment_id)
            content = json.dumps(
                {
                    "experiment": self.store.get(experiment_id).model_dump(by_alias=True, mode="json"),
                    "summary": summary.model_dump(by_alias=True, mode="json"),
                    "phaseEvents": [
                        event.model_dump(by_alias=True, mode="json")
                        for event in export_timeline.events
                    ] if isinstance(export_timeline, V2ExperimentTimeline) else [],
                    "records": [
                        json.loads(line)
                        for line in self.store.privacy_safe_jsonl(experiment_id).splitlines()
                        if line
                    ],
                },
                sort_keys=True,
                indent=2,
            )
            media_type = "application/json"
        else:
            raise ValueError("EWPS export format must be jsonl, json, or csv.")
        export_dir = get_settings().data_dir / "ewps-exports"
        export_dir.mkdir(parents=True, exist_ok=True)
        target = export_dir / f"{experiment_id}.{format_name}"
        temporary = target.with_suffix(target.suffix + ".tmp")
        temporary.write_text(content, encoding="utf-8", newline="")
        temporary.replace(target)
        return content, media_type, target

    def save_export(self, experiment_id: str, format_name: str) -> ExportSaveResult:
        _content, _media_type, path = self.export(experiment_id, format_name)
        return ExportSaveResult(
            savedPath=str(path),
            filename=path.name,
            format=format_name,
            folderOpenAvailable=True,
        )

    def lab_status(self) -> LabStatus:
        return self.lab.status()

    def lab_prerequisites(self) -> LabStatus:
        return self.lab.prerequisites()

    def lab_create(self) -> LabStatus:
        return self.lab.create()

    def lab_teardown(self) -> LabStatus:
        current = self.current()
        if current and current.status in {"RUNNING", "PAUSED"} and any(
            path_id.startswith("lab-path-") for path_id in current.candidate_path_ids
        ):
            raise ValueError("Stop the controlled-lab experiment before teardown.")
        return self.lab.teardown()

    def lab_profile(self, path_id: str, profile: LabProfileName) -> LabStatus:
        current = self.current()
        if current and current.status in {"CREATED", "RUNNING", "PAUSED"}:
            raise ValueError("Stop the active experiment before changing a lab profile directly.")
        return self.lab.apply_profile(path_id, profile)

    def lab_prepare_scenario(self, scenario_id: LabScenarioName) -> LabStatus:
        current = self.current()
        if current and current.status in {"CREATED", "RUNNING", "PAUSED"}:
            raise ValueError("Stop the active experiment before preparing or resetting a scenario.")
        return self.lab.prepare_scenario(scenario_id)

    def lab_advance_scenario(self) -> LabScenarioAdvanceResponse:
        requested_at = self._utcnow()
        with self._cycle_lock:
            session = self.current()
            if (
                not isinstance(session, V2ExperimentSession)
                or session.status != "RUNNING"
                or session.source_mode != "CONTROLLED_DUAL_PATH"
            ):
                raise ValueError("A running controlled dual-path experiment is required to advance a phase.")
            if not self.lab.binding_is_current(session.lab_instance_id, session.lab_topology_version):
                raise ValueError("The active experiment's controlled-lab binding is no longer current.")
            transition = self.lab.advance_scenario(requested_at)
            event = V2ExperimentEvent(
                eventId=f"ewps-event-{uuid.uuid4()}",
                eventType=(
                    "SCENARIO_PHASE_CHANGED"
                    if transition.application_succeeded else "SCENARIO_PHASE_APPLY_FAILED"
                ),
                timestamp=transition.requested_at,
                completedAt=transition.completed_at,
                experimentId=session.experiment_id,
                scenarioId=transition.scenario_id,
                previousPhaseIndex=transition.previous_phase_index,
                previousPhaseId=transition.previous_phase_id,
                newPhaseIndex=transition.new_phase_index,
                newPhaseId=transition.new_phase_id,
                applicationSucceeded=transition.application_succeeded,
                labInstanceId=transition.lab_instance_id,
                affectedPathIds=transition.affected_path_ids,
                pathProfiles=transition.path_profiles,
                verification=transition.verification,
                detail=transition.detail,
            )
            self.store.append_event(event)
            return LabScenarioAdvanceResponse(status=self.lab.status(), event=event)

    def lab_verify(self) -> LabStatus:
        return self.lab.verify()

    def shutdown(self) -> None:
        with self._lock:
            active_id = self._active_id
            self._run.clear()
            self._stop.set()
            self._scheduler_wake.set()
            if active_id:
                try:
                    session = self.store.get(active_id)
                    if session.status == "RUNNING":
                        self.store.transition(active_id, "PAUSED")
                except (KeyError, ValueError):
                    pass
            thread = self._thread
        if thread and thread is not threading.current_thread():
            thread.join(timeout=8.0)
        self.lab.shutdown()


_v2_service: EWPSV2Service | None = None


def get_ewps_v2_service() -> EWPSV2Service:
    global _v2_service
    expected = get_settings().data_dir / "ewps-research.sqlite3"
    if _v2_service is None or _v2_service.store.path != expected:
        _v2_service = EWPSV2Service(get_ewps_store(), get_ewps_lab())
    return _v2_service
