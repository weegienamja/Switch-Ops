from datetime import datetime, timedelta, timezone
import json
import sqlite3
from types import SimpleNamespace

import pytest

from app.ewps_engine import EWPSDecisionEngine
from app.ewps_models import EWPSConfig, EvidenceInput, ExperimentCreateRequest, RawMetrics
from app.ewps_service import EWPSService
from app.ewps_store import EWPSResearchStore
from app.ewps_telemetry import ProbeResult
from app.ewps_v2_engine import EWPSV2DecisionEngine
from app.ewps_v2_models import (
    EWPSV2Config,
    V2CandidatePath,
    V2EvidenceInput,
    V2ExperimentCreateRequest,
    V2ExperimentTimeline,
    V2RawMetrics,
)
from app.ewps_v2_service import EWPSV2Service, V2EngineRuntime, V2InternalCandidate


def config(**updates) -> EWPSV2Config:
    values = {
        "pPerfMin": 0,
        "unavailableFailureThreshold": 2,
        "unavailableReprobeCycles": 3,
        "hysteresis": {
            "minimumImprovement": 0,
            "minimumDwellSeconds": 0,
            "minimumEvidenceSeconds": 0,
            "recoveryHoldDownSeconds": 0,
        },
    }
    values.update(updates)
    return EWPSV2Config.model_validate(values)


def v2_request(run_config: EWPSV2Config | None = None) -> V2ExperimentCreateRequest:
    return V2ExperimentCreateRequest(
        name="PRIVATE V2 OPERATOR NAME",
        workloadLabel="Controlled calibration",
        candidatePathIds=["path-a", "path-b"],
        config=run_config or config(),
    )


def v2_inputs(step: int):
    validated = datetime(2026, 1, 1, tzinfo=timezone.utc) + timedelta(seconds=5 * step)
    values = []
    for path_id, latency, topology in (
        ("path-a", 16.0 + step, "weak_inference"),
        ("path-b", 24.0 - step / 2, "reciprocal_independent_direct"),
    ):
        values.append((
            path_id,
            V2RawMetrics(
                latencyMs=latency,
                rollingLatencyMs=latency,
                jitterMs=1,
                rollingJitterMs=1,
                lossPct=0,
                rollingLossPct=0,
                sampleCount=5,
                lossSampleCount=20,
                probeOutcomes=[True] * 5,
                reachable=True,
                routingMetricsUsable=True,
                telemetryState="validated",
                candidateLifecycle="VIABLE",
            ),
            V2EvidenceInput(
                ageSeconds=0,
                meanMs=latency,
                stddevMs=1,
                effectiveSamples=20,
                topologyEvidence=topology,
                collectionStartedAt=validated - timedelta(seconds=1),
                observationValidatedAt=validated,
                collectionDurationMs=1000,
            ),
        ))
    return values


def recorded_v2_store(tmp_path):
    store = EWPSResearchStore(tmp_path / "ewps-v2.sqlite3")
    session = store.create_v2(v2_request())
    store.transition(session.experiment_id, "RUNNING")
    engine = EWPSV2DecisionEngine(session.config)
    start = datetime(2026, 1, 1, tzinfo=timezone.utc)
    for step in range(6):
        store.append(
            session.experiment_id,
            engine.evaluate(start + timedelta(seconds=5 * step), v2_inputs(step)),
        )
    store.transition(session.experiment_id, "COMPLETED")
    return store, session.experiment_id


def candidate(path_id: str) -> V2InternalCandidate:
    return V2InternalCandidate(public=V2CandidatePath(
        pathId=path_id,
        displayLabel=path_id,
        adapterName="Test adapter",
        sourceKind="controlled_lab",
        topologyEvidence="reciprocal_independent_direct",
        topologyDetail="Privacy-safe synthetic candidate.",
    ))


def successful_probe(path_id: str, latency: float = 15.0) -> ProbeResult:
    now = datetime.now(timezone.utc)
    return ProbeResult(
        path_id=path_id,
        observed_at=now,
        collection_started_at=now - timedelta(milliseconds=50),
        observation_validated_at=now,
        collection_duration_ms=50,
        raw=RawMetrics(latencyMs=latency, jitterMs=1, lossPct=0, sampleCount=5, reachable=True),
        probe_outcomes=(True, True, True, True, True),
    )


def failed_probe(path_id: str) -> ProbeResult:
    now = datetime.now(timezone.utc)
    return ProbeResult(
        path_id=path_id,
        observed_at=now,
        collection_started_at=now - timedelta(milliseconds=50),
        observation_validated_at=None,
        collection_duration_ms=50,
        raw=RawMetrics(sampleCount=5, reachable=False),
        probe_outcomes=(False, False, False, False, False),
        failure_reason="complete_probe_failure",
    )


def lifecycle_service(tmp_path, monkeypatch):
    store = EWPSResearchStore(tmp_path / "lifecycle.sqlite3")
    service = EWPSV2Service(store, SimpleNamespace(candidates=lambda: []))
    only = candidate("path-a")
    monkeypatch.setattr(service, "_catalog", lambda: [only])
    session = store.create_v2(V2ExperimentCreateRequest(
        name="Lifecycle",
        workloadLabel="Controlled calibration",
        candidatePathIds=["path-a"],
        config=config(),
    ))
    store.transition(session.experiment_id, "RUNNING")
    service._active_id = session.experiment_id
    service._runtime = V2EngineRuntime(session.config)
    service._candidates = {"path-a": only}
    return service, store, session.experiment_id


def test_v02_schema_timeline_summary_and_raw_replay_inputs_are_preserved(tmp_path):
    store, experiment_id = recorded_v2_store(tmp_path)
    with sqlite3.connect(store.path) as connection:
        assert connection.execute("PRAGMA user_version").fetchone()[0] == 2
    timeline = store.timeline(experiment_id)
    summary = store.summary(experiment_id)
    assert isinstance(timeline, V2ExperimentTimeline)
    assert len(timeline.decisions) == 6
    assert summary.total_samples == 12
    assert set(summary.performance_confidence_per_path) == {"path-a", "path-b"}
    assert set(summary.topology_confidence_per_path) == {"path-a", "path-b"}
    assert set(summary.pairwise_disagreement_matrix) == {
        "lowest_latency", "lowest_loss", "performance_only", "ewps", "ewps_hysteresis"
    }
    first = timeline.decisions[0].calculations[0]
    assert first.raw.probe_outcomes == [True] * 5
    assert first.evidence.collection_started_at is not None
    assert first.evidence.observation_validated_at is not None
    assert first.confidence.performance > 0
    assert first.confidence.topology_penalty >= 1


def test_v02_replay_is_deterministic_and_override_never_mutates_record(tmp_path):
    store, experiment_id = recorded_v2_store(tmp_path)
    service = EWPSV2Service(store, SimpleNamespace(candidates=lambda: []))
    first = service.replay(experiment_id)
    second = service.replay(experiment_id)
    assert first.deterministic_digest == second.deterministic_digest
    assert first.decisions == second.decisions
    original = store.get(experiment_id)
    changed = service.replay(experiment_id, original.config.model_copy(update={"alpha": 2.0}))
    assert changed.deterministic_digest != first.deterministic_digest
    assert store.get(experiment_id).config.alpha == original.config.alpha


def test_v01_replay_dispatch_matches_legacy_engine_bit_for_bit(tmp_path):
    store = EWPSResearchStore(tmp_path / "compat.sqlite3")
    request = ExperimentCreateRequest(
        name="Legacy",
        workloadLabel="Baseline",
        candidatePathIds=["path-a"],
        config=EWPSConfig(pMin=0.01, hysteresis={
            "minimumImprovement": 0,
            "minimumDwellSeconds": 0,
            "minimumEvidenceSeconds": 0,
            "recoveryHoldDownSeconds": 0,
        }),
    )
    session = store.create(request)
    store.transition(session.experiment_id, "RUNNING")
    engine = EWPSDecisionEngine(session.config)
    start = datetime(2026, 1, 1, tzinfo=timezone.utc)
    for step in range(4):
        latency = 15.0 + step
        store.append(session.experiment_id, engine.evaluate(start + timedelta(seconds=5 * step), [(
            "path-a",
            RawMetrics(latencyMs=latency, jitterMs=1, lossPct=0, sampleCount=3, reachable=True),
            EvidenceInput(ageSeconds=4, meanMs=latency, stddevMs=1, effectiveSamples=3 * (step + 1), topologyEvidence="weak_inference"),
        )]))
    store.transition(session.experiment_id, "COMPLETED")
    legacy = EWPSService(store).replay(session.experiment_id)
    versioned = EWPSV2Service(store, SimpleNamespace(candidates=lambda: [])).replay(session.experiment_id)
    assert versioned.deterministic_digest == legacy.deterministic_digest
    assert versioned.decisions == legacy.decisions
    assert versioned.config == legacy.config


def test_v01_created_or_paused_session_is_replay_only_not_a_v02_current_session(tmp_path):
    store = EWPSResearchStore(tmp_path / "legacy-current.sqlite3")
    legacy = store.create(ExperimentCreateRequest(
        name="Legacy replay only",
        workloadLabel="Baseline",
        candidatePathIds=["path-a"],
        config=EWPSConfig(),
    ))
    service = EWPSV2Service(store, SimpleNamespace(candidates=lambda: []))
    assert service.current() is None
    assert service.get(legacy.experiment_id).ewps_model_version == "0.1.0"


def test_candidate_unavailable_does_not_inflate_transient_failure_count(tmp_path, monkeypatch):
    service, store, experiment_id = lifecycle_service(tmp_path, monkeypatch)
    monkeypatch.setattr(service, "_measure", lambda item, count: failed_probe(item.public.path_id))
    for _ in range(7):
        service.sample_once()
    timeline = store.timeline(experiment_id)
    summary = store.summary(experiment_id)
    lifecycles = [point.calculations[0].raw.candidate_lifecycle for point in timeline.decisions]
    assert "PERSISTENTLY_UNAVAILABLE" in lifecycles
    assert summary.unavailable_candidate_count == 1
    assert summary.candidate_unavailable_events == 1
    assert summary.transient_failures_on_viable_paths == 0


def test_persistently_unavailable_candidate_is_boundedly_reprobed_and_recovers(tmp_path, monkeypatch):
    service, store, experiment_id = lifecycle_service(tmp_path, monkeypatch)
    results = iter([
        failed_probe("path-a"),
        failed_probe("path-a"),
        successful_probe("path-a"),
        successful_probe("path-a"),
    ])
    calls = 0

    def measure(item, count):
        nonlocal calls
        calls += 1
        return next(results)

    monkeypatch.setattr(service, "_measure", measure)
    for _ in range(5):
        service.sample_once()
    timeline = store.timeline(experiment_id)
    summary = store.summary(experiment_id)
    states = [point.calculations[0].raw.candidate_lifecycle for point in timeline.decisions]
    assert calls == 4
    assert "reprobe_deferred" in [point.calculations[0].raw.telemetry_state for point in timeline.decisions]
    assert "RECOVERING" in states
    assert states[-1] == "VIABLE"
    assert summary.recovery_events == 1
    assert summary.unavailable_candidate_count == 0


def test_v02_privacy_safe_export_contains_replay_fields_and_fixed_local_path(tmp_path, monkeypatch):
    from app import ewps_v2_service as service_mod

    store, experiment_id = recorded_v2_store(tmp_path)
    service = EWPSV2Service(store, SimpleNamespace(candidates=lambda: []))
    monkeypatch.setattr(service_mod, "get_settings", lambda: SimpleNamespace(data_dir=tmp_path / "data"))
    saved = service.save_export(experiment_id, "jsonl")
    target = tmp_path / "data" / "ewps-exports" / f"{experiment_id}.jsonl"
    assert saved.saved_path == str(target)
    assert target.is_file()
    content = target.read_text(encoding="utf-8")
    assert "PRIVATE V2 OPERATOR NAME" not in content
    record = json.loads(content.splitlines()[0])
    assert record["schema_version"] == 2
    assert record["ewps_version"] == "0.2.0"
    assert record["raw"]["probe_outcomes"] == [True] * 5
    assert record["model"]["beta"] == pytest.approx(0.25)
    assert record["evidence"]["observation_validated_at"]
    assert record["decision"]["switch_blocked_by"] == "shadow_mode"
    for forbidden in ("password", "cookie", "authorization", "https://", "192.168."):
        assert forbidden not in content.lower()
