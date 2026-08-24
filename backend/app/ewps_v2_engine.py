"""Deterministic EWPS v0.2 calculation and shadow-decision engine."""
from __future__ import annotations

from datetime import datetime
import math
from typing import Iterable

from .ewps_engine import (
    HysteresisState,
    evidence_density,
    freshness,
    latency_stability,
    topology_confidence,
)
from .ewps_models import AlgorithmChoice
from .ewps_v2_models import (
    EWPSV2Config,
    V2ConfidenceComponents,
    V2DecisionPoint,
    V2EWPSCalculation,
    V2EvidenceInput,
    V2RawMetrics,
)


def performance_confidence(
    freshness_value: float,
    stability_value: float,
    density_value: float,
    config: EWPSV2Config,
) -> float:
    """Return normalized weighted geometric performance confidence.

    The topology component is deliberately absent.  The weight model validates
    that ``w_f + w_s + w_d = 1``.
    """
    components = (
        min(1.0, max(0.0, freshness_value)),
        min(1.0, max(0.0, stability_value)),
        min(1.0, max(0.0, density_value)),
    )
    weights = (
        config.weights.freshness,
        config.weights.stability,
        config.weights.density,
    )
    result = 1.0
    for component, weight in zip(components, weights, strict=True):
        if weight == 0:
            continue
        if component <= 0 or not math.isfinite(component):
            return 0.0
        result *= component**weight
    return min(1.0, max(0.0, result)) if math.isfinite(result) else 0.0


def topology_penalty(topology_value: float, beta: float) -> float:
    """Bounded structural penalty ``1 + beta * (1 - T_c)``."""
    topology_value = min(1.0, max(0.0, topology_value))
    result = 1.0 + beta * (1.0 - topology_value)
    return result if math.isfinite(result) else math.inf


def v2_raw_performance_cost(raw: V2RawMetrics, config: EWPSV2Config) -> float | None:
    """Return the v0.2 cost input based on bounded rolling estimates."""
    required = (
        (config.latency_weight, raw.rolling_latency_ms),
        (config.jitter_weight, raw.rolling_jitter_ms),
        (config.loss_weight, raw.rolling_loss_pct),
    )
    if not raw.routing_metrics_usable or raw.reachable is False:
        return None
    if any(weight > 0 and value is None for weight, value in required):
        return None
    values = [value for _, value in required if value is not None]
    if any(not math.isfinite(value) or value < 0 for value in values):
        return None
    result = (
        config.latency_weight * (raw.rolling_latency_ms or 0.0)
        + config.jitter_weight * (raw.rolling_jitter_ms or 0.0)
        + config.loss_weight * (raw.rolling_loss_pct or 0.0)
    )
    return result if math.isfinite(result) else None


def calculate_v2_path(
    path_id: str,
    raw: V2RawMetrics,
    evidence: V2EvidenceInput,
    config: EWPSV2Config,
) -> V2EWPSCalculation:
    ft = freshness(evidence.age_seconds, config.lambda_decay)
    sv = latency_stability(evidence.mean_ms, evidence.stddev_ms)
    dn = evidence_density(evidence.effective_samples, config.density_k)
    p_perf = performance_confidence(ft, sv, dn, config)
    tc = topology_confidence(evidence.topology_evidence)
    penalty = topology_penalty(tc, config.beta)
    raw_cost = v2_raw_performance_cost(raw, config)
    reasons: list[str] = []

    if raw.reachable is False:
        state = "UNREACHABLE"
        reasons.append("complete_probe_failure")
    elif raw_cost is None:
        state = "TELEMETRY_UNAVAILABLE"
        reasons.append("required_routing_metrics_unavailable")
    elif evidence.topology_evidence == "contradictory":
        state = "TOPOLOGY_CONFLICT"
        reasons.append("explicit_structural_conflict")
    elif evidence.effective_samples <= 0:
        state = "PERFORMANCE_EVIDENCE_INSUFFICIENT"
        reasons.append("no_effective_samples")
    elif p_perf < config.p_perf_min:
        state = "PERFORMANCE_EVIDENCE_INSUFFICIENT"
        reasons.append("below_performance_evidence_threshold")
    elif evidence.topology_evidence == "unknown":
        state = "ELIGIBLE_TOPOLOGY_UNKNOWN"
        reasons.append("topology_unknown")
    elif evidence.topology_evidence in {"weak_inference", "strong_inference"}:
        state = "ELIGIBLE_TOPOLOGY_WEAK"
        reasons.append("topology_weak")
    else:
        state = "ELIGIBLE"

    eligible = state in {
        "ELIGIBLE",
        "ELIGIBLE_TOPOLOGY_WEAK",
        "ELIGIBLE_TOPOLOGY_UNKNOWN",
    }
    ewps_cost: float | None = None
    if eligible and raw_cost is not None:
        confidence_divisor = 1.0 if config.alpha == 0 else p_perf**config.alpha
        if confidence_divisor > 0:
            candidate = raw_cost / confidence_divisor * penalty
            ewps_cost = candidate if math.isfinite(candidate) else None
    if eligible and ewps_cost is None:
        eligible = False
        state = "PERFORMANCE_EVIDENCE_INSUFFICIENT"
        reasons.append("non_finite_cost")

    return V2EWPSCalculation(
        pathId=path_id,
        raw=raw,
        evidence=evidence,
        confidence=V2ConfidenceComponents(
            freshness=ft,
            stability=sv,
            density=dn,
            performance=p_perf,
            topology=tc,
            topologyPenalty=penalty,
        ),
        rawCost=raw_cost,
        ewpsCost=ewps_cost,
        eligible=eligible,
        eligibilityState=state,
        valid=raw_cost is not None,
        reasons=reasons,
    )


def _choice(
    algorithm: str,
    calculations: Iterable[V2EWPSCalculation],
    key,
    reason: str,
) -> AlgorithmChoice:
    candidates = [item for item in calculations if key(item) is not None]
    if not candidates:
        return AlgorithmChoice(
            algorithm=algorithm,
            pathId=None,
            cost=None,
            reason="No path has sufficient current evidence for this strategy.",
        )
    selected = min(candidates, key=lambda item: (float(key(item)), item.path_id))
    return AlgorithmChoice(
        algorithm=algorithm,
        pathId=selected.path_id,
        cost=float(key(selected)),
        reason=reason,
    )


def v2_comparison_choices(calculations: list[V2EWPSCalculation]) -> list[AlgorithmChoice]:
    latency = _choice(
        "lowest_latency",
        calculations,
        lambda item: item.raw.latency_ms if item.raw.latency_ms is not None and item.raw.reachable is not False else None,
        "Lowest current validated probe latency.",
    )
    loss = _choice(
        "lowest_loss",
        calculations,
        lambda item: item.raw.rolling_loss_pct if item.valid else None,
        "Lowest bounded rolling packet-loss estimate; path ID breaks exact ties.",
    )
    performance = _choice(
        "performance_only",
        calculations,
        lambda item: item.raw_cost if item.valid else None,
        "Lowest v0.2 rolling performance cost without evidence penalties.",
    )
    ewps = _choice(
        "ewps",
        calculations,
        lambda item: item.ewps_cost if item.eligible else None,
        "Lowest eligible v0.2 evidence-weighted cost without hysteresis.",
    )
    return [latency, loss, performance, ewps]


def _weakest_performance_component(item: V2EWPSCalculation) -> str:
    components = {
        "freshness": item.confidence.freshness,
        "stability": item.confidence.stability,
        "density": item.confidence.density,
    }
    return min(components, key=lambda key: (components[key], key))


def v2_explanation(
    calculations: list[V2EWPSCalculation],
    algorithms: list[AlgorithmChoice],
    hysteresis,
) -> str:
    if hysteresis.suppressed:
        return hysteresis.reason
    by_id = {item.path_id: item for item in calculations}
    preferred = by_id.get(hysteresis.preferred_path_id or "")
    if preferred is None:
        states = ", ".join(f"{item.path_id}: {item.eligibility_state.lower()}" for item in calculations)
        return f"No EWPS recommendation is available. {states or 'No candidates were evaluated.'}"
    lowest = next((item for item in algorithms if item.algorithm == "lowest_latency"), None)
    if lowest and lowest.path_id and lowest.path_id != preferred.path_id:
        faster = by_id[lowest.path_id]
        weakest = _weakest_performance_component(faster)
        topology_note = (
            f" Its topology penalty is {faster.confidence.topology_penalty:.3f}."
            if faster.confidence.topology_penalty > 1.0
            else ""
        )
        return (
            f"{faster.path_id} has the lower current latency, but its {weakest} evidence component "
            f"limits performance confidence to {faster.confidence.performance:.3f}.{topology_note} "
            f"{preferred.path_id} has the lower eligible EWPS cost ({preferred.ewps_cost:.3f})."
        )
    return (
        f"{preferred.path_id} remains the shadow recommendation with performance confidence "
        f"{preferred.confidence.performance:.3f}, topology confidence {preferred.confidence.topology:.3f}, "
        f"and EWPS cost {preferred.ewps_cost:.3f}."
    )


def _v2_events(
    calculations: list[V2EWPSCalculation],
    previous: dict[str, V2EWPSCalculation],
    algorithms: list[AlgorithmChoice],
    previous_algorithms: dict[str, str | None],
    hysteresis,
) -> list[str]:
    events: list[str] = []
    for item in calculations:
        prior = previous.get(item.path_id)
        if item.raw.candidate_unavailable_event:
            events.append(f"candidate_unavailable:{item.path_id}")
        if item.raw.transient_failure:
            events.append(f"telemetry_failure:{item.path_id}")
        if item.raw.recovery_event:
            events.append(f"path_recovery:{item.path_id}")
        if item.raw.telemetry_state == "evidence_stale":
            events.append(f"evidence_staleness_injected:{item.path_id}")
        if item.raw.loss_pct is not None and item.raw.rolling_loss_pct is not None:
            if item.raw.loss_pct >= 20 and item.raw.rolling_loss_pct < item.raw.loss_pct:
                events.append(f"isolated_loss_smoothed:{item.path_id}")
            elif item.raw.rolling_loss_pct >= 10:
                events.append(f"rolling_loss_event:{item.path_id}")
        if prior:
            if prior.confidence.freshness >= 0.5 > item.confidence.freshness:
                events.append(f"telemetry_became_stale:{item.path_id}")
            if item.confidence.stability < prior.confidence.stability - 0.2:
                events.append(f"path_impairment:{item.path_id}:variance")
            if prior.eligible and not item.eligible:
                events.append(f"path_became_ineligible:{item.path_id}:{item.eligibility_state.lower()}")
            if not prior.eligible and item.eligible:
                events.append(f"evidence_recovery:{item.path_id}")
            if prior.evidence.topology_evidence != item.evidence.topology_evidence:
                events.append(f"topology_confidence_changed:{item.path_id}")
    for choice in algorithms:
        previous_path = previous_algorithms.get(choice.algorithm)
        if previous_path is not None and previous_path != choice.path_id:
            events.append(f"algorithm_preference_crossing:{choice.algorithm}")
    if hysteresis.recommendation_changed:
        events.append("ewps_recommendation_change")
    if hysteresis.suppressed:
        events.append(f"recommendation_suppressed:{hysteresis.switch_blocked_by}")
    return events


class EWPSV2DecisionEngine:
    """Stateful v0.2 engine shared by live, lab, replay, and simulator inputs."""

    def __init__(self, config: EWPSV2Config) -> None:
        self.config = config.model_copy(deep=True)
        self.hysteresis = HysteresisState()
        self.previous: dict[str, V2EWPSCalculation] = {}
        self.previous_algorithms: dict[str, str | None] = {}
        self.decision_index = 0

    def evaluate(
        self,
        timestamp: datetime,
        path_inputs: list[tuple[str, V2RawMetrics, V2EvidenceInput]],
    ) -> V2DecisionPoint:
        calculations = [
            calculate_v2_path(path_id, raw, evidence, self.config)
            for path_id, raw, evidence in sorted(path_inputs, key=lambda value: value[0])
        ]
        algorithms = v2_comparison_choices(calculations)
        hysteresis = self.hysteresis.decide(timestamp, calculations, self.config)
        algorithms.append(AlgorithmChoice(
            algorithm="ewps_hysteresis",
            pathId=hysteresis.preferred_path_id,
            cost=next(
                (item.ewps_cost for item in calculations if item.path_id == hysteresis.preferred_path_id),
                None,
            ),
            reason=hysteresis.reason,
        ))
        events = _v2_events(
            calculations,
            self.previous,
            algorithms,
            self.previous_algorithms,
            hysteresis,
        )
        point = V2DecisionPoint(
            timestamp=timestamp,
            decisionIndex=self.decision_index,
            calculations=calculations,
            algorithms=algorithms,
            hysteresis=hysteresis,
            events=events,
            explanation=v2_explanation(calculations, algorithms, hysteresis),
        )
        self.previous = {item.path_id: item for item in calculations}
        self.previous_algorithms = {item.algorithm: item.path_id for item in algorithms}
        self.decision_index += 1
        return point
