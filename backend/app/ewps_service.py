"""Experiment lifecycle, live sampling, replay, and export orchestration."""
from __future__ import annotations

from collections import defaultdict, deque
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import statistics
import threading
from typing import Deque

from .config import get_settings
from .ewps_engine import EWPSDecisionEngine
from .ewps_models import (
    CandidatePath,
    EWPSConfig,
    EvidenceInput,
    ExperimentCreateRequest,
    ExperimentSession,
    ExperimentSummary,
    ExperimentTimeline,
    RawMetrics,
    ReplayResult,
)
from .ewps_store import EWPSResearchStore, get_ewps_store
from .ewps_telemetry import InternalCandidate, candidate_catalog, measure_candidate
from .live_state import get_live_state


class EWPSEngineRuntime:
    def __init__(self, config: EWPSConfig) -> None:
        self.engine = EWPSDecisionEngine(config)
        self.latencies: dict[str, Deque[float]] = defaultdict(
            lambda: deque(maxlen=config.rolling_window)
        )
        self.sample_weights: dict[str, Deque[float]] = defaultdict(
            lambda: deque(maxlen=config.rolling_window)
        )
        self.last_valid: dict[str, datetime] = {}


class EWPSService:
    def __init__(self, store: EWPSResearchStore | None = None) -> None:
        self.store = store or get_ewps_store()
        self._lock = threading.RLock()
        self._active_id: str | None = None
        self._runtime: EWPSEngineRuntime | None = None
        self._candidates: dict[str, InternalCandidate] = {}
        self._stop = threading.Event()
        self._run = threading.Event()
        self._thread: threading.Thread | None = None
        # A process interruption cannot leave an apparently live collector in
        # durable state. Recover the newest interrupted run as safely paused.
        for recorded in self.store.list(500):
            if recorded.status == "RUNNING":
                recovered = self.store.transition(recorded.experiment_id, "PAUSED")
                if self._active_id is None:
                    self._active_id = recovered.experiment_id

    def candidates(self) -> list[CandidatePath]:
        return [item.public for item in candidate_catalog()]

    def create(self, request: ExperimentCreateRequest) -> ExperimentSession:
        available = {item.public.path_id for item in candidate_catalog()}
        missing = sorted(set(request.candidate_path_ids) - available)
        if missing:
            raise ValueError("One or more selected candidate paths are no longer active.")
        with self._lock:
            if self._active_id:
                active = self.store.get(self._active_id)
                if active.status in {"RUNNING", "PAUSED"}:
                    raise ValueError("Stop the active EWPS experiment before creating another.")
            session = self.store.create(request)
            self._active_id = session.experiment_id
            return session

    def list(self, limit: int = 50) -> list[ExperimentSession]:
        return self.store.list(limit)

    def get(self, experiment_id: str) -> ExperimentSession:
        return self.store.get(experiment_id)

    def current(self) -> ExperimentSession | None:
        with self._lock:
            if self._active_id:
                try:
                    return self.store.get(self._active_id)
                except KeyError:
                    self._active_id = None
            sessions = self.store.list(1)
            return sessions[0] if sessions else None

    def _resolve_candidates(self, session: ExperimentSession) -> dict[str, InternalCandidate]:
        available = {item.public.path_id: item for item in candidate_catalog()}
        missing = [path_id for path_id in session.candidate_path_ids if path_id not in available]
        if missing:
            raise ValueError("A recorded candidate adapter is not currently active.")
        return {path_id: available[path_id] for path_id in session.candidate_path_ids}

    def _restore_runtime(self, session: ExperimentSession) -> EWPSEngineRuntime:
        runtime = EWPSEngineRuntime(session.config)
        timeline = self.store.timeline(session.experiment_id)
        for point in timeline.decisions:
            inputs = []
            for item in point.calculations:
                inputs.append((item.path_id, item.raw, item.evidence))
                if item.valid and item.raw.latency_ms is not None:
                    runtime.latencies[item.path_id].append(item.raw.latency_ms)
                    received = item.raw.sample_count * (1.0 - (item.raw.loss_pct or 0.0) / 100.0)
                    runtime.sample_weights[item.path_id].append(max(0.0, received))
                    runtime.last_valid[item.path_id] = point.timestamp
            runtime.engine.evaluate(point.timestamp, inputs)
        return runtime

    def start(self, experiment_id: str) -> ExperimentSession:
        with self._lock:
            session = self.store.get(experiment_id)
            if session.status not in {"CREATED", "PAUSED"}:
                raise ValueError("Only a created or paused experiment can be started.")
            if self._active_id and self._active_id != experiment_id:
                active = self.store.get(self._active_id)
                if active.status in {"RUNNING", "PAUSED"}:
                    raise ValueError("Another EWPS experiment is active.")
            self._candidates = self._resolve_candidates(session)
            if self._runtime is None or self._active_id != experiment_id:
                self._runtime = self._restore_runtime(session)
            self._active_id = experiment_id
            session = self.store.transition(experiment_id, "RUNNING")
            self._stop.clear()
            self._run.set()
            if self._thread is None or not self._thread.is_alive():
                self._thread = threading.Thread(
                    target=self._collector_loop,
                    name="switchops-ewps-shadow-collector",
                    daemon=True,
                )
                self._thread.start()
            return session

    def pause(self, experiment_id: str) -> ExperimentSession:
        with self._lock:
            if experiment_id != self._active_id:
                raise ValueError("This is not the active EWPS experiment.")
            self._run.clear()
            return self.store.transition(experiment_id, "PAUSED")

    def stop(self, experiment_id: str) -> tuple[ExperimentSession, ExperimentSummary]:
        with self._lock:
            session = self.store.get(experiment_id)
            if session.status not in {"CREATED", "RUNNING", "PAUSED"}:
                raise ValueError("This EWPS experiment is already complete.")
            self._run.clear()
            self._stop.set()
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

    def _collector_loop(self) -> None:
        while not self._stop.is_set():
            if not self._run.wait(timeout=0.5):
                continue
            try:
                self.sample_once()
            except Exception:
                # A failed cycle is non-fatal. Individual measurement failures
                # are normally represented as immutable calculation records.
                pass
            with self._lock:
                if not self._active_id:
                    break
                interval = self.store.get(self._active_id).config.sample_interval_seconds
            if self._stop.wait(timeout=interval):
                break

    def sample_once(self) -> None:
        with self._lock:
            experiment_id = self._active_id
            runtime = self._runtime
            candidates = list(self._candidates.values())
        if not experiment_id or runtime is None:
            raise ValueError("No EWPS experiment is active.")
        session = self.store.get(experiment_id)
        if session.status != "RUNNING":
            return
        # Refresh only the non-sensitive public evidence mapping. The source IP
        # stays in the private in-memory candidate object and is never logged.
        refreshed = {item.public.path_id: item for item in candidate_catalog()}
        results = [measure_candidate(candidate, session.config.probe_count) for candidate in candidates]
        timestamp = max((item.observed_at for item in results), default=datetime.now(timezone.utc))
        path_inputs: list[tuple[str, RawMetrics, EvidenceInput]] = []
        for result in results:
            path_id = result.path_id
            if result.raw.reachable and result.raw.latency_ms is not None:
                runtime.latencies[path_id].append(result.raw.latency_ms)
                received = result.raw.sample_count * (1.0 - (result.raw.loss_pct or 0.0) / 100.0)
                runtime.sample_weights[path_id].append(max(0.0, received))
                runtime.last_valid[path_id] = result.observed_at
            series = runtime.latencies[path_id]
            mean = statistics.fmean(series) if series else None
            stddev = statistics.pstdev(series) if len(series) > 1 else (0.0 if series else None)
            last_valid = runtime.last_valid.get(path_id)
            age = max(0.0, (timestamp - last_valid).total_seconds()) if last_valid else None
            current_candidate = refreshed.get(path_id) or self._candidates[path_id]
            evidence = EvidenceInput(
                ageSeconds=age,
                meanMs=mean,
                stddevMs=stddev,
                effectiveSamples=sum(runtime.sample_weights[path_id]),
                topologyEvidence=current_candidate.public.topology_evidence,
            )
            path_inputs.append((path_id, result.raw, evidence))
        point = runtime.engine.evaluate(timestamp, path_inputs)
        self.store.append(experiment_id, point)
        get_live_state().hub.publish(
            "ewps_decision",
            point.model_dump(by_alias=True, mode="json"),
        )

    def timeline(self, experiment_id: str) -> ExperimentTimeline:
        return self.store.timeline(experiment_id)

    def summary(self, experiment_id: str) -> ExperimentSummary:
        return self.store.summary(experiment_id)

    def replay(self, experiment_id: str, config: EWPSConfig | None = None) -> ReplayResult:
        timeline = self.store.timeline(experiment_id)
        replay_config = (config or timeline.session.config).model_copy(deep=True)
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
        digest = hashlib.sha256(canonical.encode("utf-8")).hexdigest()
        return ReplayResult(
            sourceExperimentId=experiment_id,
            config=replay_config,
            deterministicDigest=digest,
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
            content = json.dumps(
                {
                    "summary": self.store.summary(experiment_id).model_dump(by_alias=True, mode="json"),
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

    def shutdown(self) -> None:
        with self._lock:
            active_id = self._active_id
            self._run.clear()
            self._stop.set()
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


_service: EWPSService | None = None


def get_ewps_service() -> EWPSService:
    global _service
    expected = get_settings().data_dir / "ewps-research.sqlite3"
    if _service is None or _service.store.path != expected:
        _service = EWPSService(get_ewps_store())
    return _service
