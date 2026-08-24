"""Deterministic v0.2 scenarios using the production v0.2 engine."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

from .ewps_engine import EWPSDecisionEngine
from .ewps_models import EWPSConfig, EvidenceInput, RawMetrics
from .ewps_v2_engine import EWPSV2DecisionEngine
from .ewps_v2_models import (
    EWPSV2Config,
    V2EvidenceInput,
    V2RawMetrics,
    V2SimulatorRunResult,
    V2SimulatorScenario,
)


V2_SCENARIOS: tuple[V2SimulatorScenario, ...] = (
    V2SimulatorScenario(
        scenarioId="conventional-agreement",
        name="Scenario 1 · Conventional agreement",
        description="Path A is faster, stable, fresh, and densely observed.",
        expectedResearchPattern="All strategies should prefer Path A after evidence establishment.",
    ),
    V2SimulatorScenario(
        scenarioId="faster-epistemically-weak",
        name="Scenario 2 · Faster but epistemically weak",
        description="Path A remains nominally faster while variance and observation age increase.",
        expectedResearchPattern="Performance logic may retain A while EWPS can defensibly prefer B.",
    ),
    V2SimulatorScenario(
        scenarioId="raw-metric-flapping",
        name="Scenario 3 · Raw-metric flapping",
        description="Current latencies cross repeatedly around a narrow margin.",
        expectedResearchPattern="Latency-only changes repeatedly while EWPS+hysteresis may remain stable.",
    ),
    V2SimulatorScenario(
        scenarioId="evidence-outage",
        name="Scenario 4 · Evidence outage",
        description="A previously viable path stops producing validated observations without a reachability verdict.",
        expectedResearchPattern="Stored evidence ages naturally until performance confidence falls below threshold.",
    ),
    V2SimulatorScenario(
        scenarioId="recovery",
        name="Scenario 5 · Recovery",
        description="Evidence returns after an outage and must rebuild through recovery hold-down.",
        expectedResearchPattern="Recommendation recovery is delayed rather than immediate.",
    ),
    V2SimulatorScenario(
        scenarioId="experiment-001-calibration",
        name="Experiment 001 calibration",
        description="A healthy ~15 ms path has weak topology, short spikes, isolated loss, and several evidence failures.",
        expectedResearchPattern="Compare the v0.1 topology-product knife edge with separated v0.2 confidence.",
    ),
    V2SimulatorScenario(
        scenarioId="conventional-preferable",
        name="Conventional routing is preferable",
        description="The faster path also has the strongest available evidence.",
        expectedResearchPattern="EWPS should agree; disagreement is not inherently desirable.",
    ),
    V2SimulatorScenario(
        scenarioId="adversarial-model",
        name="Adversarial model configuration",
        description="An exaggerated uncertainty penalty demonstrates a possible bad EWPS choice.",
        expectedResearchPattern="Research controls must expose that EWPS can make a poor recommendation.",
    ),
)


def list_v2_scenarios() -> list[V2SimulatorScenario]:
    return [item.model_copy(deep=True) for item in V2_SCENARIOS]


def _v2_input(
    path_id: str,
    *,
    latency: float | None,
    rolling_latency: float | None,
    rolling_jitter: float | None,
    instant_loss: float | None = 0.0,
    rolling_loss: float | None = 0.0,
    age: float | None = 0.0,
    mean: float | None = None,
    stddev: float | None = None,
    samples: float = 30.0,
    topology: str = "reciprocal_independent_direct",
    reachable: bool | None = True,
    usable: bool = True,
    lifecycle: str = "VIABLE",
    telemetry_state: str = "validated",
) -> tuple[str, V2RawMetrics, V2EvidenceInput]:
    now = datetime(2026, 1, 1, tzinfo=timezone.utc)
    return (
        path_id,
        V2RawMetrics(
            latencyMs=latency,
            rollingLatencyMs=rolling_latency,
            jitterMs=rolling_jitter,
            rollingJitterMs=rolling_jitter,
            lossPct=instant_loss,
            rollingLossPct=rolling_loss,
            sampleCount=5 if latency is not None else 0,
            lossSampleCount=int(samples),
            probeOutcomes=[True] * min(5, int(samples)),
            reachable=reachable,
            routingMetricsUsable=usable,
            telemetryState=telemetry_state,
            candidateLifecycle=lifecycle,
        ),
        V2EvidenceInput(
            ageSeconds=age,
            meanMs=mean if mean is not None else rolling_latency,
            stddevMs=stddev if stddev is not None else rolling_jitter,
            effectiveSamples=samples,
            topologyEvidence=topology,
            collectionStartedAt=now if latency is not None else None,
            observationValidatedAt=now if latency is not None else None,
            collectionDurationMs=40.0 if latency is not None else None,
        ),
    )


def _scenario_inputs(scenario_id: str, step: int):
    a_latency = 15.0
    b_latency = 25.0
    a = dict(latency=a_latency, rolling_latency=a_latency, rolling_jitter=0.8, samples=min(50.0, 5.0 * (step + 1)))
    b = dict(latency=b_latency, rolling_latency=b_latency, rolling_jitter=1.0, samples=min(50.0, 5.0 * (step + 1)))
    if scenario_id in {"conventional-agreement", "conventional-preferable"}:
        pass
    elif scenario_id == "faster-epistemically-weak":
        if step >= 6:
            # A remains cheaper on raw performance while confidence in that
            # estimate weakens enough to create a legitimate disagreement.
            a.update(rolling_jitter=10.0, stddev=10.0, age=(step - 5) * 5.0, samples=12.0)
    elif scenario_id == "raw-metric-flapping":
        crossing = 2.5 if step % 2 else -2.5
        a.update(latency=22.0 + crossing, rolling_latency=22.0, rolling_jitter=5.5, stddev=5.5)
        b.update(latency=22.0 - crossing, rolling_latency=22.5, rolling_jitter=1.0, stddev=1.0)
    elif scenario_id == "evidence-outage":
        if step >= 6:
            a.update(latency=None, age=(step - 5) * 5.0, reachable=None, telemetry_state="evidence_stale")
    elif scenario_id == "recovery":
        if 5 <= step < 10:
            a.update(latency=None, age=(step - 4) * 12.0, reachable=None, telemetry_state="evidence_stale")
        elif step >= 10:
            a.update(samples=float((step - 9) * 5), lifecycle="RECOVERING" if step < 12 else "VIABLE")
    elif scenario_id == "experiment-001-calibration":
        a.update(topology="weak_inference", rolling_latency=15.0 + (7.0 if step in {4, 11} else 0.0))
        if step in {7, 13}:
            a.update(instant_loss=20.0, rolling_loss=2.0)
        if 8 <= step <= 10:
            a.update(latency=None, age=(step - 7) * 5.0, reachable=None, telemetry_state="transient_failure")
        b.update(latency=None, rolling_latency=None, rolling_jitter=None, rolling_loss=None, age=None, samples=0, reachable=False, usable=False, lifecycle="PERSISTENTLY_UNAVAILABLE", telemetry_state="candidate_unavailable")
    elif scenario_id == "adversarial-model":
        a.update(rolling_latency=14.0, rolling_jitter=8.0, stddev=8.0, age=8.0, topology="weak_inference")
        b.update(rolling_latency=34.0, rolling_jitter=0.5, stddev=0.5)
    else:
        raise KeyError(scenario_id)
    return [
        _v2_input("path-a", **a),
        _v2_input("path-b", **b),
    ]


def _v1_calibration() -> dict[str, object]:
    config = EWPSConfig()
    engine = EWPSDecisionEngine(config)
    start = datetime(2026, 1, 1, tzinfo=timezone.utc)
    decisions = []
    for step in range(16):
        age = 4.0 if step not in {8, 9, 10} else float((step - 7) * 5)
        raw = RawMetrics(
            latencyMs=15.0 if step not in {8, 9, 10} else None,
            jitterMs=1.0 if step not in {8, 9, 10} else None,
            lossPct=33.333333 if step in {7, 13} else (0.0 if step not in {8, 9, 10} else None),
            sampleCount=3,
            reachable=step not in {8, 9, 10},
        )
        evidence = EvidenceInput(
            ageSeconds=age,
            meanMs=15.0,
            stddevMs=1.0,
            effectiveSamples=float(min(30, (step + 1) * 3)),
            topologyEvidence="weak_inference",
        )
        decisions.append(engine.evaluate(start + timedelta(seconds=5 * step), [("path-a", raw, evidence)]))
    return {
        "eligibleDecisionPoints": sum(point.calculations[0].eligible for point in decisions),
        "ineligibleDecisionPoints": sum(not point.calculations[0].eligible for point in decisions),
        "averageCompositeConfidence": sum(point.calculations[0].certainty.composite for point in decisions) / len(decisions),
        "semanticVersion": "0.1.0",
    }


def run_v2_scenario(scenario_id: str, config: EWPSV2Config) -> V2SimulatorRunResult:
    scenario = next((item for item in V2_SCENARIOS if item.scenario_id == scenario_id), None)
    if scenario is None:
        raise KeyError(scenario_id)
    run_config = config.model_copy(deep=True)
    if scenario_id == "adversarial-model" and config == EWPSV2Config():
        run_config = config.model_copy(update={"alpha": 4.0, "beta": 2.0, "p_perf_min": 0.10})
    engine = EWPSV2DecisionEngine(run_config)
    start = datetime(2026, 1, 1, tzinfo=timezone.utc)
    decisions = [
        engine.evaluate(start + timedelta(seconds=5 * step), _scenario_inputs(scenario_id, step))
        for step in range(16)
    ]
    choices = {
        algorithm: [
            next(item.path_id for item in point.algorithms if item.algorithm == algorithm)
            for point in decisions
        ]
        for algorithm in ("lowest_latency", "performance_only", "ewps", "ewps_hysteresis")
    }
    summary = {
        "decisionPoints": len(decisions),
        "recommendationChanges": {
            algorithm: sum(left != right for left, right in zip(paths, paths[1:]))
            for algorithm, paths in choices.items()
        },
        "disagreementPoints": sum(
            len({path for path in (choices["lowest_latency"][index], choices["performance_only"][index], choices["ewps"][index], choices["ewps_hysteresis"][index]) if path}) > 1
            for index in range(len(decisions))
        ),
        "suppressedRecommendations": sum(point.hysteresis.suppressed for point in decisions),
        "shadowMode": True,
        "outcomeClaim": "A difference is recorded, not labelled objectively better.",
    }
    return V2SimulatorRunResult(
        scenario=scenario,
        config=run_config,
        decisions=decisions,
        summary=summary,
        v1Comparison=_v1_calibration() if scenario_id == "experiment-001-calibration" else None,
    )
