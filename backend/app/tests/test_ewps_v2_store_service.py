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
    LabPhaseTransitionResult,
    LabPathStatus,
    LabStatus,
    V2CandidatePath,
    V2CandidateSnapshot,
    V2EvidenceInput,
    V2ExperimentEvent,
    V2ExperimentCreateRequest,
    V2ExperimentTimeline,
    V2NormalizedNetemConfig,
    V2PhasePathProfile,
    V2RawMetrics,
    V2ScenarioPhaseSnapshot,
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
        sourceMode="REAL_INTERFACES",
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
    session = store.create_v2(
        v2_request(),
        candidate_snapshot=[snapshot("path-a"), snapshot("path-b")],
        lab_instance_id=None,
        lab_topology_version=None,
        initial_verification_status="NOT_APPLICABLE",
        controlled_scenario=None,
    )
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


def snapshot(path_id: str) -> V2CandidateSnapshot:
    return V2CandidateSnapshot(
        pathId=path_id,
        displayLabel=path_id,
        adapterName="Test adapter",
        sourceKind="real_interface",
        topologyEvidence="reciprocal_independent_direct",
        topologyDetail="Privacy-safe synthetic candidate.",
    )


def candidate(path_id: str) -> V2InternalCandidate:
    return V2InternalCandidate(public=V2CandidatePath(
        pathId=path_id,
        displayLabel=path_id,
        adapterName="Test adapter",
        sourceKind="real_interface",
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
        sourceMode="REAL_INTERFACES",
        candidatePathIds=["path-a"],
        config=config(),
    ), candidate_snapshot=[snapshot("path-a")], lab_instance_id=None,
        lab_topology_version=None, initial_verification_status="NOT_APPLICABLE",
        controlled_scenario=None)
    store.transition(session.experiment_id, "RUNNING")
    service._active_id = session.experiment_id
    service._runtime = V2EngineRuntime(session.config)
    service._candidates = {"path-a": only}
    return service, store, session.experiment_id


def phase_profile(profile_id: str, delay_ms: float, jitter_ms: float) -> V2PhasePathProfile:
    config_value = V2NormalizedNetemConfig(
        delayMs=delay_ms,
        jitterMs=jitter_ms,
        delayCorrelationPct=10 if profile_id != "fast-noisy" else 25,
        distribution="normal" if profile_id == "fast-noisy" else None,
    )
    return V2PhasePathProfile(
        requestedProfileId=profile_id,
        appliedProfileId=profile_id,
        requestedConfiguration=config_value,
        appliedConfiguration=config_value,
        verification="PASSED",
        verificationDetail="deterministic test proof",
    )


def phase_snapshot(phase_index: int) -> V2ScenarioPhaseSnapshot:
    noisy = phase_index == 1
    return V2ScenarioPhaseSnapshot(
        scenarioId="faster-epistemically-weak",
        phaseIndex=phase_index,
        phaseId="fast-noisy" if noisy else "baseline",
        labInstanceId="11111111-1111-4111-8111-111111111111",
        pathProfiles={
            "lab-path-a": phase_profile("fast-noisy" if noisy else "fast-stable", 8, 12 if noisy else .5),
            "lab-path-b": phase_profile("slow-stable", 28, .5),
        },
    )


class PhaseLab:
    def __init__(self, *, fail=False):
        self.phase = 0
        self.fail = fail

    def binding_is_current(self, instance_id, topology_version):
        return instance_id == "11111111-1111-4111-8111-111111111111"

    def current_phase_snapshot(self):
        return phase_snapshot(self.phase)

    def advance_scenario(self, requested_at):
        target = phase_snapshot(1)
        if not self.fail:
            self.phase = 1
        return LabPhaseTransitionResult(
            requestedAt=requested_at,
            completedAt=requested_at + timedelta(milliseconds=20),
            scenarioId="faster-epistemically-weak",
            previousPhaseIndex=0,
            previousPhaseId="baseline",
            newPhaseIndex=1,
            newPhaseId="fast-noisy",
            applicationSucceeded=not self.fail,
            labInstanceId=target.lab_instance_id,
            affectedPathIds=["lab-path-a"],
            pathProfiles=target.path_profiles,
            verification="FAILED" if self.fail else "PASSED",
            detail="mismatch and rollback verified" if self.fail else "verified",
        )

    def status(self):
        snapshot_value = phase_snapshot(self.phase)
        return LabStatus(
            available=True,
            ready=True,
            state="LAB_READY",
            prerequisitesPassed=True,
            labInstanceId=snapshot_value.lab_instance_id,
            topologyVersion="switchops-ewps-contained-dual-path-v1",
            message="test lab",
            scenarioId=snapshot_value.scenario_id,
            scenarioPhase=snapshot_value.phase_index,
            scenarioPhaseId=snapshot_value.phase_id,
            scenarioPhaseCount=3,
            paths=[
                LabPathStatus(pathId="lab-path-a", displayLabel="Path A", profile=("fast-noisy" if self.phase else "fast-stable")),
                LabPathStatus(pathId="lab-path-b", displayLabel="Path B", profile="slow-stable"),
            ],
        )


def controlled_service(tmp_path, monkeypatch, *, fail=False):
    store = EWPSResearchStore(tmp_path / ("controlled-fail.sqlite3" if fail else "controlled.sqlite3"))
    initial = phase_snapshot(0)
    request = V2ExperimentCreateRequest(
        name="Controlled provenance",
        workloadLabel="Background streaming",
        sourceMode="CONTROLLED_DUAL_PATH",
        controlledScenario="faster-epistemically-weak",
        candidatePathIds=["lab-path-a", "lab-path-b"],
        config=config(),
    )
    controlled_snapshots = [
        V2CandidateSnapshot(
            pathId=path_id,
            displayLabel=path_id,
            adapterName="Controlled",
            sourceKind="controlled_lab",
            topologyEvidence="reciprocal_independent_direct",
            topologyDetail="Contained test path.",
        )
        for path_id in request.candidate_path_ids
    ]
    session = store.create_v2(
        request,
        candidate_snapshot=controlled_snapshots,
        lab_instance_id=initial.lab_instance_id,
        lab_topology_version="switchops-ewps-contained-dual-path-v1",
        initial_verification_status="VERIFIED",
        controlled_scenario="faster-epistemically-weak",
        initial_scenario_phase=initial,
    )
    lab = PhaseLab(fail=fail)
    service = EWPSV2Service(store, lab)
    store.transition(session.experiment_id, "RUNNING")
    service._active_id = session.experiment_id
    service._runtime = V2EngineRuntime(session.config)
    service._candidates = {
        path_id: V2InternalCandidate(public=V2CandidatePath(
            pathId=path_id,
            displayLabel=path_id,
            adapterName="Controlled",
            sourceKind="controlled_lab",
            topologyEvidence="reciprocal_independent_direct",
            topologyDetail="Contained test path.",
        ))
        for path_id in request.candidate_path_ids
    }
    monkeypatch.setattr(service, "_measure", lambda item, count: successful_probe(
        item.public.path_id,
        9 if item.public.path_id == "lab-path-a" else 29,
    ))
    return service, store, session.experiment_id


def test_v02_schema_timeline_summary_and_raw_replay_inputs_are_preserved(tmp_path):
    store, experiment_id = recorded_v2_store(tmp_path)
    with sqlite3.connect(store.path) as connection:
        assert connection.execute("PRAGMA user_version").fetchone()[0] == 4
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
    assert timeline.session.source_mode == "REAL_INTERFACES"
    assert [item.path_id for item in timeline.session.candidate_snapshot] == ["path-a", "path-b"]
    assert all(point.cadence is None for point in timeline.decisions)
    assert summary.configured_interval_seconds == 5
    assert summary.observed_start_to_start_seconds.mean is None
    assert summary.cadence_overrun_count == 0


def test_five_second_start_to_start_cadence_waits_only_one_second_after_four_second_collection():
    starts = [0.0]
    for _ in range(2):
        collection_completed = starts[-1] + 4.0
        remaining = EWPSV2Service._seconds_until_next_cycle(5.0, starts[-1], collection_completed)
        starts.append(collection_completed + remaining)

    assert starts == [0.0, 5.0, 10.0]
    assert starts[1] - starts[0] == pytest.approx(5.0)
    assert starts[1] - starts[0] != pytest.approx(9.0)
    assert EWPSV2Service._seconds_until_next_cycle(5.0, 10.0, 16.0) == 0.0


def test_live_cycle_cadence_is_append_only_in_timeline_summary_and_export(tmp_path, monkeypatch):
    service, store, experiment_id = lifecycle_service(tmp_path, monkeypatch)
    monkeypatch.setattr(service, "_measure", lambda item, count: successful_probe(item.public.path_id))
    completed_monotonic = iter([4.0, 9.0, 16.0])
    service._monotonic = lambda: next(completed_monotonic)
    base = datetime(2026, 1, 1, tzinfo=timezone.utc)
    completion_times = iter([
        base + timedelta(seconds=4),
        base + timedelta(seconds=9),
        base + timedelta(seconds=16),
    ])
    service._utcnow = lambda: next(completion_times)

    service.sample_once(cycle_started_at=base, cycle_started_monotonic=0.0)
    service.sample_once(
        cycle_started_at=base + timedelta(seconds=5),
        cycle_started_monotonic=5.0,
        actual_start_to_start_seconds=5.0,
    )
    service.sample_once(
        cycle_started_at=base + timedelta(seconds=10),
        cycle_started_monotonic=10.0,
        actual_start_to_start_seconds=5.0,
    )

    timeline = store.timeline(experiment_id)
    assert [point.cadence.actual_start_to_start_seconds for point in timeline.decisions] == [None, 5.0, 5.0]
    assert [point.cadence.collection_duration_ms for point in timeline.decisions] == [4000.0, 4000.0, 6000.0]
    assert [point.cadence.cadence_overrun_count for point in timeline.decisions] == [0, 0, 1]
    summary = store.summary(experiment_id)
    assert summary.configured_interval_seconds == 5.0
    assert summary.observed_start_to_start_seconds.mean == 5.0
    assert summary.observed_collection_duration_ms.mean == pytest.approx(4666.6666667)
    assert summary.cadence_overrun_count == 1

    record = json.loads(store.privacy_safe_jsonl(experiment_id).splitlines()[-1])
    assert record["schema_version"] == 4
    assert record["record_type"] == "measurement"
    assert record["scenario"] is None
    assert record["cadence"] == {
        "actual_start_to_start_seconds": 5.0,
        "cadence_overrun_count": 1,
        "collection_duration_ms": 6000.0,
        "configured_interval_seconds": 5.0,
        "cycle_completed_at": "2026-01-01T00:00:16Z",
        "cycle_started_at": "2026-01-01T00:00:10Z",
    }
    csv_header = store.privacy_safe_csv(experiment_id).splitlines()[0]
    assert "actual_start_to_start_seconds" in csv_header
    assert "cadence_overrun_count" in csv_header


def test_source_binding_columns_are_database_immutable(tmp_path):
    store, experiment_id = recorded_v2_store(tmp_path)
    with sqlite3.connect(store.path) as connection, pytest.raises(sqlite3.IntegrityError, match="source binding is immutable"):
        connection.execute(
            "UPDATE ewps_experiments SET source_mode = 'CONTROLLED_DUAL_PATH' WHERE experiment_id = ?",
            (experiment_id,),
        )


def test_v02_replay_is_deterministic_and_override_never_mutates_record(tmp_path):
    store, experiment_id = recorded_v2_store(tmp_path)
    service = EWPSV2Service(store, SimpleNamespace(candidates=lambda: []))
    first = service.replay(experiment_id)
    second = service.replay(experiment_id)
    assert first.deterministic_digest == "ffb80352b65fe9c7cb71e39336658bc9a3eddd4cb64e6754b8fc924602011606"
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
    assert record["schema_version"] == 4
    assert record["source_mode"] == "REAL_INTERFACES"
    assert record["candidate_provenance"]["path_id"] == record["path_id"]
    assert record["ewps_version"] == "0.2.0"
    assert record["raw"]["probe_outcomes"] == [True] * 5
    assert record["model"]["beta"] == pytest.approx(0.25)
    assert record["evidence"]["observation_validated_at"]
    assert record["decision"]["switch_blocked_by"] == "shadow_mode"
    for forbidden in ("password", "cookie", "authorization", "https://", "192.168."):
        assert forbidden not in content.lower()


def test_schema_v4_records_authoritative_phase_transition_once_and_replay_preserves_it(tmp_path, monkeypatch):
    service, store, experiment_id = controlled_service(tmp_path, monkeypatch)
    service.sample_once()
    response = service.lab_advance_scenario()
    service.sample_once()

    assert response.event is not None
    assert response.event.event_type == "SCENARIO_PHASE_CHANGED"
    assert response.event.previous_phase_id == "baseline"
    assert response.event.new_phase_id == "fast-noisy"
    assert response.event.affected_path_ids == ["lab-path-a"]
    assert response.event.path_profiles["lab-path-a"].requested_profile_id == "fast-noisy"
    assert response.event.path_profiles["lab-path-a"].applied_profile_id == "fast-noisy"
    assert response.event.path_profiles["lab-path-b"].applied_profile_id == "slow-stable"

    timeline = store.timeline(experiment_id)
    assert timeline.session.initial_scenario_phase.phase_id == "baseline"
    assert [point.scenario_phase.phase_id for point in timeline.decisions] == ["baseline", "fast-noisy"]
    assert len(timeline.events) == 1
    assert timeline.decisions[0].timestamp <= timeline.events[0].timestamp <= timeline.decisions[1].timestamp
    replay = service.replay(experiment_id)
    assert replay.events == timeline.events
    assert [point.scenario_phase for point in replay.decisions] == [
        point.scenario_phase for point in timeline.decisions
    ]
    assert [point.calculations for point in replay.decisions] == [
        point.calculations for point in service.replay(experiment_id).decisions
    ]

    records = [json.loads(line) for line in store.privacy_safe_jsonl(experiment_id).splitlines()]
    measurements = [record for record in records if record["record_type"] == "measurement"]
    events = [record for record in records if record["record_type"] == "phase_event"]
    summaries = [record for record in records if record["record_type"] == "phase_summary"]
    assert {record["schema_version"] for record in records} == {4}
    assert [record["scenario"]["phase_id"] for record in measurements[::2]] == ["baseline", "fast-noisy"]
    assert len(events) == 1
    assert events[0]["event"]["verification"] == "PASSED"
    assert {record["phase_summary"]["phase_id"] for record in summaries} == {"baseline", "fast-noisy"}
    csv_export = store.privacy_safe_csv(experiment_id)
    assert "SCENARIO_PHASE_CHANGED" in csv_export
    assert '""fast-noisy""' in csv_export

    with sqlite3.connect(store.path), pytest.raises(sqlite3.IntegrityError, match="events are immutable"):
        connection = sqlite3.connect(store.path)
        try:
            connection.execute(
                "UPDATE ewps_experiment_events SET event_type = 'SCENARIO_PHASE_APPLY_FAILED' WHERE event_id = ?",
                (timeline.events[0].event_id,),
            )
        finally:
            connection.close()


def test_failed_phase_application_is_an_immutable_failure_and_phase_zero_remains_authoritative(tmp_path, monkeypatch):
    service, store, experiment_id = controlled_service(tmp_path, monkeypatch, fail=True)
    service.sample_once()
    response = service.lab_advance_scenario()
    service.sample_once()
    timeline = store.timeline(experiment_id)

    assert response.event.event_type == "SCENARIO_PHASE_APPLY_FAILED"
    assert response.event.application_succeeded is False
    assert response.event.verification == "FAILED"
    assert response.status.scenario_phase == 0
    assert [point.scenario_phase.phase_id for point in timeline.decisions] == ["baseline", "baseline"]
    assert len(timeline.events) == 1


def test_schema_three_cadence_export_remains_readable(tmp_path, monkeypatch):
    service, store, experiment_id = lifecycle_service(tmp_path, monkeypatch)
    monkeypatch.setattr(service, "_measure", lambda item, count: successful_probe(item.public.path_id))
    service._monotonic = lambda: 4.0
    service._utcnow = lambda: datetime(2026, 1, 1, 0, 0, 4, tzinfo=timezone.utc)
    service.sample_once(
        cycle_started_at=datetime(2026, 1, 1, tzinfo=timezone.utc),
        cycle_started_monotonic=0.0,
    )
    with sqlite3.connect(store.path) as connection:
        connection.execute(
            "UPDATE ewps_experiments SET release_id = 'ewps-v0.2.2-alpha' WHERE experiment_id = ?",
            (experiment_id,),
        )
    record = json.loads(store.privacy_safe_jsonl(experiment_id).splitlines()[0])
    assert record["schema_version"] == 3
    assert "record_type" not in record
    assert V2ExperimentTimeline.model_validate(store.timeline(experiment_id)).decisions[0].scenario_phase is None


def test_a_previous_release_that_wrote_schema_v4_still_exports_as_schema_v4(
    tmp_path, monkeypatch
):
    """Releasing must not reclassify experiments an earlier build recorded.

    Sessions store the release that wrote them and no schema field, so the
    export path once asked "is this the newest release?" to mean "is this
    schema v4?". Those answers diverge the moment a release ships without a
    schema change, and the older release's v4 records would have silently
    dropped their phase events and summaries.
    """
    service, store, experiment_id = lifecycle_service(tmp_path, monkeypatch)
    monkeypatch.setattr(service, "_measure", lambda item, count: successful_probe(item.public.path_id))
    service._monotonic = lambda: 4.0
    service._utcnow = lambda: datetime(2026, 1, 1, 0, 0, 4, tzinfo=timezone.utc)
    service.sample_once(
        cycle_started_at=datetime(2026, 1, 1, tzinfo=timezone.utc),
        cycle_started_monotonic=0.0,
    )
    with sqlite3.connect(store.path) as connection:
        connection.execute(
            "UPDATE ewps_experiments SET release_id = 'ewps-v0.2.3-alpha' WHERE experiment_id = ?",
            (experiment_id,),
        )
    record = json.loads(store.privacy_safe_jsonl(experiment_id).splitlines()[0])
    assert record["schema_version"] == 4
    assert "record_type" in record
    assert "schema_version,record_type" in store.privacy_safe_csv(experiment_id)


def test_every_schema_v4_release_is_a_declared_release_id():
    # Keeps the set from drifting into naming a release the record model would
    # refuse to load.
    import typing

    from app.ewps_v2_models import (
        EWPS_V2_RELEASE_ID,
        SCHEMA_V4_RELEASE_IDS,
        V2ExperimentSession,
    )

    declared = set(
        typing.get_args(V2ExperimentSession.model_fields["release_id"].annotation)
    )
    assert SCHEMA_V4_RELEASE_IDS <= declared
    assert EWPS_V2_RELEASE_ID in SCHEMA_V4_RELEASE_IDS


def test_phase_provenance_is_not_an_ewps_model_input():
    engine_without = EWPSV2DecisionEngine(config())
    engine_with = EWPSV2DecisionEngine(config())
    timestamp = datetime(2026, 1, 1, tzinfo=timezone.utc)
    plain = engine_without.evaluate(timestamp, v2_inputs(0))
    annotated = engine_with.evaluate(timestamp, v2_inputs(0)).model_copy(
        update={"scenario_phase": phase_snapshot(0)}
    )
    assert annotated.calculations == plain.calculations
    assert annotated.algorithms == plain.algorithms
    assert annotated.hysteresis == plain.hysteresis
