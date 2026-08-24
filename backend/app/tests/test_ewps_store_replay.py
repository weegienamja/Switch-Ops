from datetime import datetime, timedelta, timezone
import json
import sqlite3

import pytest

from app.ewps_engine import EWPSDecisionEngine
from app.ewps_models import EWPSConfig, EvidenceInput, ExperimentCreateRequest, RawMetrics
from app.ewps_service import EWPSService
from app.ewps_simulator import list_scenarios, run_scenario
from app.ewps_store import EWPSResearchStore


def request(config: EWPSConfig | None = None) -> ExperimentCreateRequest:
    return ExperimentCreateRequest(
        name="PRIVATE OPERATOR SESSION NAME",
        workloadLabel="YouTube 4K",
        candidatePathIds=["path-a", "path-b"],
        config=config or EWPSConfig(
            pMin=0.01,
            hysteresis={
                "minimumImprovement": 0,
                "minimumDwellSeconds": 0,
                "minimumEvidenceSeconds": 0,
                "recoveryHoldDownSeconds": 0,
            },
        ),
    )


def inputs(step: int):
    return [
        (
            "path-a",
            RawMetrics(latencyMs=20 + step, jitterMs=1, lossPct=0, sampleCount=3, reachable=True),
            EvidenceInput(ageSeconds=0, meanMs=20 + step, stddevMs=1, effectiveSamples=10 + step, topologyEvidence="reciprocal_independent_direct"),
        ),
        (
            "path-b",
            RawMetrics(latencyMs=28 - step, jitterMs=2, lossPct=0, sampleCount=3, reachable=True),
            EvidenceInput(ageSeconds=0, meanMs=28 - step, stddevMs=2, effectiveSamples=10 + step, topologyEvidence="one_sided_direct"),
        ),
    ]


def recorded_store(tmp_path):
    store = EWPSResearchStore(tmp_path / "ewps.sqlite3")
    session = store.create(request())
    store.transition(session.experiment_id, "RUNNING")
    engine = EWPSDecisionEngine(session.config)
    start = datetime(2026, 1, 1, tzinfo=timezone.utc)
    for step in range(8):
        store.append(session.experiment_id, engine.evaluate(start + timedelta(seconds=5 * step), inputs(step)))
    store.transition(session.experiment_id, "COMPLETED")
    return store, session.experiment_id


def test_experiment_lifecycle_and_material_records_are_immutable(tmp_path):
    store, experiment_id = recorded_store(tmp_path)
    session = store.get(experiment_id)
    assert session.status == "COMPLETED"
    assert session.total_measurements == 16
    assert session.decision_points == 8
    with pytest.raises(ValueError):
        store.transition(experiment_id, "RUNNING")
    with sqlite3.connect(store.path) as connection, pytest.raises(sqlite3.IntegrityError):
        connection.execute(
            "UPDATE ewps_observations SET path_id = 'tampered' WHERE experiment_id = ?",
            (experiment_id,),
        )


def test_timeline_summary_and_algorithm_disagreement_are_queryable(tmp_path):
    store, experiment_id = recorded_store(tmp_path)
    timeline = store.timeline(experiment_id)
    summary = store.summary(experiment_id)
    assert len(timeline.decisions) == 8
    assert all(len(point.calculations) == 2 for point in timeline.decisions)
    assert summary.total_samples == 16
    assert summary.measurements_per_path == {"path-a": 8, "path-b": 8}
    assert 0 <= summary.algorithm_disagreement_rate <= 1
    assert set(summary.average_confidence_per_path) == {"path-a", "path-b"}


def test_privacy_safe_exports_omit_operator_name_and_network_content(tmp_path):
    store, experiment_id = recorded_store(tmp_path)
    jsonl = store.privacy_safe_jsonl(experiment_id)
    csv_text = store.privacy_safe_csv(experiment_id)
    assert "PRIVATE OPERATOR SESSION NAME" not in jsonl
    assert "PRIVATE OPERATOR SESSION NAME" not in csv_text
    for forbidden in ("192.168.1.10", "adapter-secret", "https://", "cookie", "video title"):
        assert forbidden not in jsonl.lower()
        assert forbidden not in csv_text.lower()
    records = [json.loads(line) for line in jsonl.splitlines()]
    assert records
    assert records[0]["ewps_version"] == "0.1.0"
    assert records[0]["decision"]["switch_blocked_by"] == "shadow_mode"


def test_replay_is_deterministic_and_override_does_not_modify_original(tmp_path):
    store, experiment_id = recorded_store(tmp_path)
    service = EWPSService(store)
    first = service.replay(experiment_id)
    second = service.replay(experiment_id)
    assert first.deterministic_digest == second.deterministic_digest
    assert first.decisions == second.decisions
    original = store.get(experiment_id)
    changed = service.replay(experiment_id, original.config.model_copy(update={"alpha": 2.0}))
    assert changed.deterministic_digest != first.deterministic_digest
    assert store.get(experiment_id).config.alpha == original.config.alpha


def test_every_required_simulator_scenario_uses_deterministic_engine():
    expected = {
        "fast-stable", "fast-high-variance", "stale-path", "sparse-evidence",
        "telemetry-failure", "topology-degradation", "sudden-loss", "path-recovery",
        "raw-crossings", "latency-flap-ewps-stable", "slower-path-wins",
    }
    scenarios = list_scenarios()
    assert {item.scenario_id for item in scenarios} == expected
    config = EWPSConfig()
    for scenario in scenarios:
        first = run_scenario(scenario.scenario_id, config)
        second = run_scenario(scenario.scenario_id, config)
        assert first.decisions == second.decisions
        assert len(first.decisions) == 16
        assert first.summary["shadowMode"] is True
    flap = run_scenario("latency-flap-ewps-stable", config)
    assert flap.summary["latencyRecommendationChanges"] > flap.summary["ewpsHysteresisRecommendationChanges"]
    slower = run_scenario("slower-path-wins", config)
    assert next(item.path_id for item in slower.decisions[-1].algorithms if item.algorithm == "lowest_latency") == "path-a"
    assert next(item.path_id for item in slower.decisions[-1].algorithms if item.algorithm == "ewps") == "path-b"
