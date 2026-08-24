"""Deterministic EWPS scenarios using the exact live calculation engine."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

from .ewps_engine import EWPSDecisionEngine
from .ewps_models import (
    EWPSConfig,
    EvidenceInput,
    RawMetrics,
    SimulatorRunResult,
    SimulatorScenario,
)


SCENARIOS: tuple[SimulatorScenario, ...] = (
    SimulatorScenario(scenarioId="fast-stable", name="Fast and stable path", description="A consistently fast path is supported by dense, stable evidence."),
    SimulatorScenario(scenarioId="fast-high-variance", name="Fast but high variance", description="The nominally faster path develops high latency variance."),
    SimulatorScenario(scenarioId="stale-path", name="Stale path", description="One path stops producing valid observations and freshness decays."),
    SimulatorScenario(scenarioId="sparse-evidence", name="Sparse evidence", description="A fast path has too few effective samples to establish confidence."),
    SimulatorScenario(scenarioId="telemetry-failure", name="Telemetry source failure", description="One measurement source fails without producing an infinite display cost."),
    SimulatorScenario(scenarioId="topology-degradation", name="Topology confidence degradation", description="A proven relationship becomes inferred and then contradictory."),
    SimulatorScenario(scenarioId="sudden-loss", name="Sudden loss event", description="A fast path experiences a sudden packet-loss event."),
    SimulatorScenario(scenarioId="path-recovery", name="Path recovery", description="An ineligible path recovers and is held down before recommendation."),
    SimulatorScenario(scenarioId="raw-crossings", name="Repeated raw-latency crossings", description="Two paths repeatedly exchange the lowest raw latency."),
    SimulatorScenario(scenarioId="latency-flap-ewps-stable", name="Latency flapping, EWPS stable", description="The conventional latency strategy flaps while EWPS with hysteresis stays stable."),
    SimulatorScenario(scenarioId="slower-path-wins", name="Slower path wins on evidence", description="The faster path is ineligible because its supporting evidence is insufficient."),
)


def list_scenarios() -> list[SimulatorScenario]:
    return [item.model_copy(deep=True) for item in SCENARIOS]


def _inputs(scenario_id: str, step: int) -> list[tuple[str, RawMetrics, EvidenceInput]]:
    crossing = -1 if step % 2 else 1
    values = {
        "path-a": {
            "latency": 20.0,
            "jitter": 1.0,
            "loss": 0.0,
            "reachable": True,
            "age": 0.0,
            "mean": 20.0,
            "stddev": 1.0,
            "samples": float(step + 8),
            "topology": "reciprocal_independent_direct",
        },
        "path-b": {
            "latency": 27.0,
            "jitter": 1.5,
            "loss": 0.0,
            "reachable": True,
            "age": 0.0,
            "mean": 27.0,
            "stddev": 1.5,
            "samples": float(step + 8),
            "topology": "reciprocal_independent_direct",
        },
    }
    a, b = values["path-a"], values["path-b"]
    if scenario_id == "fast-high-variance" and step >= 5:
        a.update(latency=18.0 + (24.0 if step % 2 else 0.0), jitter=18.0, mean=30.0, stddev=18.0)
    elif scenario_id == "stale-path" and step >= 5:
        a.update(reachable=False, latency=None, jitter=None, loss=None, age=(step - 4) * 10.0)
    elif scenario_id == "sparse-evidence":
        a.update(latency=17.0, mean=17.0, samples=1.0)
    elif scenario_id == "telemetry-failure" and 5 <= step <= 9:
        a.update(reachable=False, latency=None, jitter=None, loss=None, age=(step - 4) * 5.0)
    elif scenario_id == "topology-degradation":
        if 5 <= step < 9:
            a.update(topology="strong_inference")
        elif step >= 9:
            a.update(topology="contradictory")
    elif scenario_id == "sudden-loss" and step >= 6:
        a.update(loss=12.5, jitter=8.0)
    elif scenario_id == "path-recovery":
        if step < 5:
            a.update(reachable=False, latency=None, jitter=None, loss=None, age=60.0)
        else:
            a.update(latency=17.0, mean=17.0, stddev=1.0, samples=float(step - 3), age=0.0)
    elif scenario_id == "raw-crossings":
        a.update(latency=24.0 + crossing * 2.0, mean=24.0, stddev=2.0)
        b.update(latency=24.0 - crossing * 2.0, mean=24.0, stddev=2.0)
    elif scenario_id == "latency-flap-ewps-stable":
        a.update(latency=24.0 + crossing * 2.0, jitter=7.0, mean=24.0, stddev=9.0, topology="one_sided_direct")
        b.update(latency=25.0 - crossing * 1.0, jitter=1.0, mean=25.0, stddev=1.0)
    elif scenario_id == "slower-path-wins":
        a.update(latency=14.0, mean=14.0, stddev=8.0, samples=1.0, topology="weak_inference")
        b.update(latency=29.0, mean=29.0, stddev=1.0, samples=float(step + 10))
    elif scenario_id not in {item.scenario_id for item in SCENARIOS}:
        raise KeyError(scenario_id)

    result: list[tuple[str, RawMetrics, EvidenceInput]] = []
    for path_id, value in values.items():
        result.append((
            path_id,
            RawMetrics(
                latencyMs=value["latency"],
                jitterMs=value["jitter"],
                lossPct=value["loss"],
                sampleCount=3,
                reachable=bool(value["reachable"]),
            ),
            EvidenceInput(
                ageSeconds=value["age"],
                meanMs=value["mean"],
                stddevMs=value["stddev"],
                effectiveSamples=value["samples"],
                topologyEvidence=value["topology"],
            ),
        ))
    return result


def run_scenario(scenario_id: str, config: EWPSConfig) -> SimulatorRunResult:
    scenario = next((item for item in SCENARIOS if item.scenario_id == scenario_id), None)
    if scenario is None:
        raise KeyError(scenario_id)
    engine = EWPSDecisionEngine(config)
    start = datetime(2026, 1, 1, tzinfo=timezone.utc)
    decisions = [
        engine.evaluate(start + timedelta(seconds=5 * step), _inputs(scenario_id, step))
        for step in range(16)
    ]
    latency_choices = [
        next(item.path_id for item in point.algorithms if item.algorithm == "lowest_latency")
        for point in decisions
    ]
    hysteresis_choices = [
        next(item.path_id for item in point.algorithms if item.algorithm == "ewps_hysteresis")
        for point in decisions
    ]
    return SimulatorRunResult(
        scenario=scenario,
        config=config,
        decisions=decisions,
        summary={
            "decisionPoints": len(decisions),
            "latencyRecommendationChanges": sum(
                left != right for left, right in zip(latency_choices, latency_choices[1:])
            ),
            "ewpsHysteresisRecommendationChanges": sum(
                left != right for left, right in zip(hysteresis_choices, hysteresis_choices[1:])
            ),
            "suppressedRecommendations": sum(point.hysteresis.suppressed for point in decisions),
            "shadowMode": True,
        },
    )
