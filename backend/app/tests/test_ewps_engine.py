from datetime import datetime, timedelta, timezone
import math

import pytest

from app.ewps_engine import (
    EWPSDecisionEngine,
    TOPOLOGY_CONFIDENCE,
    calculate_path,
    composite_certainty,
    evidence_density,
    freshness,
    latency_stability,
)
from app.ewps_models import EWPSConfig, EvidenceInput, HysteresisConfig, RawMetrics


def raw(latency: float = 20, jitter: float = 1, loss: float = 0) -> RawMetrics:
    return RawMetrics(latencyMs=latency, jitterMs=jitter, lossPct=loss, sampleCount=3, reachable=True)


def evidence(
    *, age: float = 0, mean: float = 20, stddev: float = 1,
    samples: float = 20, topology: str = "reciprocal_independent_direct",
) -> EvidenceInput:
    return EvidenceInput(
        ageSeconds=age,
        meanMs=mean,
        stddevMs=stddev,
        effectiveSamples=samples,
        topologyEvidence=topology,
    )


def test_v01_formula_components_and_zero_mean_are_explicit():
    assert freshness(10, 0.1) == pytest.approx(math.exp(-1))
    assert latency_stability(10, 5) == pytest.approx(1 / 1.25)
    assert evidence_density(4, 0.2) == pytest.approx(1 - math.exp(-0.8))
    assert latency_stability(0, 0) == 0
    assert latency_stability(-1, 1) == 0
    assert freshness(None, 0.1) == 0


def test_stale_evidence_decays_monotonically():
    values = [freshness(age, 0.035) for age in range(0, 301, 5)]
    assert values[0] == 1
    assert all(left >= right for left, right in zip(values, values[1:]))


def test_increasing_variance_reduces_latency_stability():
    values = [latency_stability(25, sigma) for sigma in range(0, 31)]
    assert all(left >= right for left, right in zip(values, values[1:]))
    assert values[0] == 1


def test_density_saturates_and_never_exceeds_one():
    values = [evidence_density(samples, 0.35) for samples in range(0, 1_000)]
    assert all(left <= right for left, right in zip(values, values[1:]))
    assert values[-1] == pytest.approx(1)
    assert all(0 <= value <= 1 for value in values)


def test_topology_mapping_is_explicit_and_orders_existing_semantics():
    assert set(TOPOLOGY_CONFIDENCE) == {
        "reciprocal_independent_direct", "one_sided_direct", "strong_inference",
        "weak_inference", "contradictory", "unknown",
    }
    scores = {key: value[0] for key, value in TOPOLOGY_CONFIDENCE.items()}
    assert scores["reciprocal_independent_direct"] > scores["one_sided_direct"]
    assert scores["one_sided_direct"] > scores["strong_inference"] > scores["weak_inference"]
    assert scores["contradictory"] == scores["unknown"] == 0


def test_product_and_weighted_certainty_are_supported():
    product = EWPSConfig(certaintyMode="product")
    weighted = EWPSConfig(
        certaintyMode="weighted_geometric",
        weights={"freshness": 2, "stability": 1, "density": 0, "topology": 1},
    )
    assert composite_certainty(0.8, 0.9, 0.7, 0.6, product) == pytest.approx(0.3024)
    assert composite_certainty(0.8, 0.9, 0.7, 0.6, weighted) == pytest.approx(0.8**2 * 0.9 * 0.6)


def test_alpha_zero_removes_uncertainty_penalty():
    config = EWPSConfig(alpha=0, pMin=0.01)
    result = calculate_path("a", raw(), evidence(age=20, stddev=8, samples=5), config)
    assert result.eligible
    assert result.ewps_cost == pytest.approx(result.raw_cost)


def test_uncertainty_cannot_improve_cost_when_performance_is_fixed():
    config = EWPSConfig(alpha=1, pMin=0)
    certain = calculate_path("a", raw(), evidence(age=0, stddev=1, samples=30), config)
    variations = [
        calculate_path("a", raw(), evidence(age=age, stddev=stddev, samples=samples, topology=topology), config)
        for age, stddev, samples, topology in [
            (10, 1, 30, "reciprocal_independent_direct"),
            (0, 8, 30, "reciprocal_independent_direct"),
            (0, 1, 3, "reciprocal_independent_direct"),
            (0, 1, 30, "strong_inference"),
        ]
    ]
    assert certain.ewps_cost is not None
    for uncertain in variations:
        assert uncertain.certainty.composite <= certain.certainty.composite
        assert uncertain.ewps_cost is not None
        assert uncertain.ewps_cost >= certain.ewps_cost


def test_threshold_missing_and_zero_values_fail_closed_without_infinity():
    config = EWPSConfig(pMin=0.25)
    missing = calculate_path(
        "missing",
        RawMetrics(sampleCount=3, reachable=False),
        EvidenceInput(effectiveSamples=0, topologyEvidence="unknown"),
        config,
    )
    assert not missing.valid
    assert not missing.eligible
    assert missing.ewps_cost is None
    assert "missing_or_failed_telemetry" in missing.reasons
    assert "below_minimum_evidence" in missing.reasons


def test_algorithm_comparison_uses_same_inputs_and_exposes_disagreement():
    config = EWPSConfig(
        pMin=0.1,
        hysteresis=HysteresisConfig(
            minimumImprovement=0, minimumDwellSeconds=0,
            minimumEvidenceSeconds=0, recoveryHoldDownSeconds=0,
        ),
    )
    engine = EWPSDecisionEngine(config)
    point = engine.evaluate(datetime(2026, 1, 1, tzinfo=timezone.utc), [
        ("fast-weak", raw(15, 1, 0), evidence(mean=15, stddev=8, samples=3, topology="weak_inference")),
        ("slow-strong", raw(25, 1, 0), evidence(mean=25, stddev=1, samples=30)),
    ])
    choices = {item.algorithm: item.path_id for item in point.algorithms}
    assert choices["lowest_latency"] == "fast-weak"
    assert choices["performance_only"] == "fast-weak"
    assert choices["ewps"] == "slow-strong"
    assert choices["ewps_hysteresis"] == "slow-strong"


def test_hysteresis_records_each_suppression_reason_and_then_switches():
    config = EWPSConfig(
        pMin=0.01,
        hysteresis=HysteresisConfig(
            minimumImprovement=0.05,
            minimumDwellSeconds=10,
            minimumEvidenceSeconds=5,
            recoveryHoldDownSeconds=0,
        ),
    )
    engine = EWPSDecisionEngine(config)
    start = datetime(2026, 1, 1, tzinfo=timezone.utc)
    inputs = [("a", raw(20), evidence()), ("b", raw(30), evidence(mean=30))]
    first = engine.evaluate(start, inputs)
    assert first.hysteresis.suppressed
    assert first.hysteresis.switch_blocked_by == "minimum_evidence_duration"
    established = engine.evaluate(start + timedelta(seconds=5), inputs)
    assert established.hysteresis.preferred_path_id == "a"
    challenger = [("a", raw(30), evidence(mean=30)), ("b", raw(18), evidence(mean=18))]
    dwell_block = engine.evaluate(start + timedelta(seconds=8), challenger)
    assert dwell_block.hysteresis.suppressed
    assert dwell_block.hysteresis.switch_blocked_by in {"minimum_evidence_duration", "minimum_dwell_time"}
    switched = engine.evaluate(start + timedelta(seconds=16), challenger)
    assert switched.hysteresis.would_switch
    assert switched.hysteresis.preferred_path_id == "b"
    assert switched.hysteresis.switch_blocked_by == "shadow_mode"
