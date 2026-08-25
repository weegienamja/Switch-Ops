from datetime import datetime, timedelta, timezone
import math

import pytest

from app.ewps_engine import evidence_density, freshness
from app.ewps_models import HysteresisConfig
from app.ewps_v2_engine import EWPSV2DecisionEngine, calculate_v2_path
from app.ewps_v2_models import EWPSV2Config, V2EvidenceInput, V2RawMetrics
from app.ewps_v2_simulator import list_v2_scenarios, run_v2_scenario


def raw(
    latency: float = 20.0,
    jitter: float = 1.0,
    instant_loss: float = 0.0,
    rolling_loss: float = 0.0,
) -> V2RawMetrics:
    return V2RawMetrics(
        latencyMs=latency,
        rollingLatencyMs=latency,
        jitterMs=jitter,
        rollingJitterMs=jitter,
        lossPct=instant_loss,
        rollingLossPct=rolling_loss,
        sampleCount=5,
        lossSampleCount=50,
        probeOutcomes=[True] * 5,
        reachable=True,
        routingMetricsUsable=True,
        telemetryState="validated",
        candidateLifecycle="VIABLE",
    )


def evidence(
    *,
    age: float = 0.0,
    mean: float = 20.0,
    stddev: float = 1.0,
    samples: float = 50.0,
    topology: str = "reciprocal_independent_direct",
) -> V2EvidenceInput:
    validated = datetime(2026, 1, 1, tzinfo=timezone.utc)
    return V2EvidenceInput(
        ageSeconds=age,
        meanMs=mean,
        stddevMs=stddev,
        effectiveSamples=samples,
        topologyEvidence=topology,
        collectionStartedAt=validated - timedelta(seconds=4),
        observationValidatedAt=validated,
        collectionDurationMs=4000,
    )


def permissive(**updates) -> EWPSV2Config:
    values = {
        "pPerfMin": 0,
        "hysteresis": {
            "minimumImprovement": 0,
            "minimumDwellSeconds": 0,
            "minimumEvidenceSeconds": 0,
            "recoveryHoldDownSeconds": 0,
        },
    }
    values.update(updates)
    return EWPSV2Config.model_validate(values)


def test_v02_defaults_and_normalized_performance_weights():
    config = EWPSV2Config()
    assert config.lambda_decay == pytest.approx(0.035)
    assert config.density_k == pytest.approx(0.08)
    assert config.alpha == pytest.approx(1.0)
    assert config.beta == pytest.approx(0.25)
    assert config.p_perf_min == pytest.approx(0.50)
    assert config.probe_count == 5
    assert config.loss_window_probes == 50
    assert sum(config.weights.model_dump().values()) == pytest.approx(1.0)
    with pytest.raises(ValueError):
        EWPSV2Config(weights={"freshness": 0.5, "stability": 0.5, "density": 0.5})


def test_increasing_performance_uncertainty_cannot_reduce_cost():
    config = permissive()
    certain = calculate_v2_path("a", raw(), evidence(), config)
    uncertain = [
        calculate_v2_path("a", raw(), evidence(age=20), config),
        calculate_v2_path("a", raw(), evidence(stddev=12), config),
        calculate_v2_path("a", raw(), evidence(samples=4), config),
    ]
    assert certain.ewps_cost is not None
    for result in uncertain:
        assert result.confidence.performance <= certain.confidence.performance
        assert result.ewps_cost is not None
        assert result.ewps_cost >= certain.ewps_cost


def test_alpha_zero_removes_performance_penalty_but_retains_topology_penalty():
    config = permissive(alpha=0, beta=0.25)
    result = calculate_v2_path(
        "a", raw(), evidence(age=30, stddev=15, samples=2, topology="weak_inference"), config
    )
    assert result.eligible
    assert result.ewps_cost == pytest.approx(result.raw_cost * result.confidence.topology_penalty)


def test_beta_zero_removes_topology_penalty_and_topology_monotonicity():
    beta_zero = permissive(beta=0)
    weak_without_penalty = calculate_v2_path("a", raw(), evidence(topology="weak_inference"), beta_zero)
    assert weak_without_penalty.confidence.topology_penalty == 1
    assert weak_without_penalty.ewps_cost == pytest.approx(
        weak_without_penalty.raw_cost / weak_without_penalty.confidence.performance
    )

    config = permissive(beta=0.25)
    direct = calculate_v2_path("a", raw(), evidence(), config)
    one_sided = calculate_v2_path("a", raw(), evidence(topology="one_sided_direct"), config)
    weak = calculate_v2_path("a", raw(), evidence(topology="weak_inference"), config)
    unknown = calculate_v2_path("a", raw(), evidence(topology="unknown"), config)
    costs = [direct.ewps_cost, one_sided.ewps_cost, weak.ewps_cost, unknown.ewps_cost]
    assert all(value is not None for value in costs)
    assert costs == sorted(costs)
    assert unknown.eligibility_state == "ELIGIBLE_TOPOLOGY_UNKNOWN"


def test_explicit_topology_conflict_is_ineligible_but_weak_topology_is_not():
    config = permissive()
    conflict = calculate_v2_path("a", raw(), evidence(topology="contradictory"), config)
    weak = calculate_v2_path("b", raw(), evidence(topology="weak_inference"), config)
    assert not conflict.eligible
    assert conflict.eligibility_state == "TOPOLOGY_CONFLICT"
    assert weak.eligible
    assert weak.eligibility_state == "ELIGIBLE_TOPOLOGY_WEAK"


def test_freshness_uses_validation_time_and_then_decays_monotonically():
    values = [freshness(age, EWPSV2Config().lambda_decay) for age in (0, 5, 10, 30, 60)]
    assert values[0] == pytest.approx(1.0)
    assert all(left >= right for left, right in zip(values, values[1:]))
    fresh = calculate_v2_path("a", raw(), evidence(age=0), permissive())
    assert fresh.evidence.collection_duration_ms == 4000
    assert fresh.confidence.freshness == pytest.approx(1.0)


def test_v02_density_curve_saturates_materially_slower_than_v01_default():
    samples = (1, 3, 5, 10, 20, 30, 50)
    old = [evidence_density(n, 0.35) for n in samples]
    new = [evidence_density(n, 0.08) for n in samples]
    assert all(left <= right for left, right in zip(new, new[1:]))
    assert all(new_value < old_value for new_value, old_value in zip(new[:-1], old[:-1]))
    assert new[2] < 0.5
    assert new[-1] > 0.95


def test_rolling_loss_distinguishes_one_miss_from_sustained_thirty_three_percent():
    config = permissive()
    isolated = calculate_v2_path("a", raw(instant_loss=20, rolling_loss=2), evidence(), config)
    sustained = calculate_v2_path("a", raw(instant_loss=40, rolling_loss=33), evidence(), config)
    no_loss = calculate_v2_path("a", raw(), evidence(), config)
    assert isolated.raw_cost is not None and sustained.raw_cost is not None and no_loss.raw_cost is not None
    assert isolated.raw_cost < sustained.raw_cost
    assert isolated.raw_cost - no_loss.raw_cost == pytest.approx(20)
    assert sustained.raw_cost - no_loss.raw_cost == pytest.approx(330)


def test_dual_path_comparison_is_deterministic_and_can_expose_disagreement():
    config = permissive()
    timestamp = datetime(2026, 1, 1, tzinfo=timezone.utc)
    inputs = [
        ("path-a", raw(15, 10), evidence(mean=15, stddev=14, samples=6, topology="weak_inference")),
        ("path-b", raw(24, 1), evidence(mean=24, stddev=1, samples=50)),
    ]
    first = EWPSV2DecisionEngine(config).evaluate(timestamp, inputs)
    second = EWPSV2DecisionEngine(config).evaluate(timestamp, list(reversed(inputs)))
    assert first == second
    choices = {item.algorithm: item.path_id for item in first.algorithms}
    assert choices["lowest_latency"] == "path-a"
    assert choices["performance_only"] == "path-a"
    assert choices["ewps"] == "path-b"


def test_hysteresis_prevents_oscillation_when_current_latency_crosses():
    config = permissive(
        hysteresis=HysteresisConfig(
            minimumImprovement=0.08,
            minimumDwellSeconds=30,
            minimumEvidenceSeconds=0,
            recoveryHoldDownSeconds=0,
        )
    )
    engine = EWPSV2DecisionEngine(config)
    start = datetime(2026, 1, 1, tzinfo=timezone.utc)
    latency_choices: list[str | None] = []
    hysteresis_choices: list[str | None] = []
    for step in range(8):
        a_current, b_current = ((19.0, 23.0) if step % 2 == 0 else (23.0, 19.0))
        point = engine.evaluate(start + timedelta(seconds=5 * step), [
            ("path-a", raw(a_current, 1).model_copy(update={"rolling_latency_ms": 20.0}), evidence(mean=20)),
            ("path-b", raw(b_current, 1).model_copy(update={"rolling_latency_ms": 21.0}), evidence(mean=21)),
        ])
        choices = {item.algorithm: item.path_id for item in point.algorithms}
        latency_choices.append(choices["lowest_latency"])
        hysteresis_choices.append(choices["ewps_hysteresis"])
    assert len(set(latency_choices)) == 2
    assert hysteresis_choices == ["path-a"] * 8


def test_simulator_includes_agreement_slower_choice_and_adversarial_controls():
    ids = {item.scenario_id for item in list_v2_scenarios()}
    assert {
        "conventional-agreement",
        "faster-epistemically-weak",
        "experiment-001-calibration",
        "conventional-preferable",
        "adversarial-model",
    } <= ids
    agreement = run_v2_scenario("conventional-agreement", EWPSV2Config())
    weak = run_v2_scenario("faster-epistemically-weak", EWPSV2Config())
    recovery = run_v2_scenario("recovery", EWPSV2Config())
    adversarial = run_v2_scenario("adversarial-model", EWPSV2Config())
    calibration = run_v2_scenario("experiment-001-calibration", EWPSV2Config())
    assert agreement.source_mode == "SIMULATOR"
    assert agreement.summary["disagreementPoints"] == 0
    assert weak.summary["disagreementPoints"] > 0
    weak_last = {item.algorithm: item.path_id for item in weak.decisions[-1].algorithms}
    assert weak_last["lowest_latency"] == "path-a"
    assert weak_last["performance_only"] == "path-a"
    assert weak_last["ewps"] == "path-b"
    assert recovery.summary["recommendationChanges"]["ewps"] >= 2
    assert recovery.summary["suppressedRecommendations"] > 0
    assert adversarial.summary["disagreementPoints"] > 0
    adversarial_last = {item.algorithm: item.path_id for item in adversarial.decisions[-1].algorithms}
    assert adversarial_last["performance_only"] == "path-a"
    assert adversarial_last["ewps"] == "path-b"
    assert calibration.v1_comparison is not None
    assert calibration.v1_comparison["semanticVersion"] == "0.1.0"
    rerun = run_v2_scenario("faster-epistemically-weak", EWPSV2Config())
    assert weak.decisions == rerun.decisions
