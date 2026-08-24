"""Deterministic EWPS v0.1 calculation and shadow-decision engine.

This module is deliberately free of network and persistence code. Live probes,
replay, and the simulator all feed the same typed inputs through this engine.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
import math
from typing import Iterable

from .ewps_models import (
    AlgorithmChoice,
    CertaintyComponents,
    DecisionPoint,
    EWPSCalculation,
    EWPSConfig,
    EvidenceInput,
    HysteresisDecision,
    RawMetrics,
    TopologyEvidenceKey,
)


TOPOLOGY_CONFIDENCE_MAPPING_VERSION = "switchops-evidence-v1/ewps-map-v1"

# These keys are derived from SwitchOps' existing relationship semantics:
# reciprocal LabEdge observations, one-sided direct CDP/LLDP/local-adapter
# observations, inferred forwarding relationships, ambiguity/conflict, unknown.
TOPOLOGY_CONFIDENCE: dict[TopologyEvidenceKey, tuple[float, str]] = {
    "reciprocal_independent_direct": (
        1.0,
        "Reciprocal independently observed direct relationship.",
    ),
    "one_sided_direct": (
        0.85,
        "One-sided direct observation; the peer side is not independently proven.",
    ),
    "strong_inference": (
        0.60,
        "Strong inferred forwarding relationship supported by current evidence.",
    ),
    "weak_inference": (
        0.30,
        "Weak or incomplete inferred relationship.",
    ),
    "contradictory": (
        0.0,
        "Contradictory or ambiguous relationship evidence is unusable.",
    ),
    "unknown": (0.0, "No usable relationship evidence is available."),
}


def _unit(value: float) -> float:
    if not math.isfinite(value):
        return 0.0
    return min(1.0, max(0.0, value))


def freshness(age_seconds: float | None, lambda_decay: float) -> float:
    """F_t = exp(-lambda * age), with invalid ages failing closed."""
    if age_seconds is None or not math.isfinite(age_seconds) or age_seconds < 0:
        return 0.0
    return _unit(math.exp(-lambda_decay * age_seconds))


def latency_stability(mean_ms: float | None, stddev_ms: float | None) -> float:
    """S_v = 1 / (1 + (sigma / mu)^2) for positive latency metrics.

    A non-positive mean is outside the formula's valid domain and explicitly
    produces no stability confidence. Other metric families can add their own
    stability function without reusing coefficient of variation blindly.
    """
    if (
        mean_ms is None
        or stddev_ms is None
        or not math.isfinite(mean_ms)
        or not math.isfinite(stddev_ms)
        or mean_ms <= 0
        or stddev_ms < 0
    ):
        return 0.0
    ratio = stddev_ms / mean_ms
    return _unit(1.0 / (1.0 + ratio * ratio))


def evidence_density(effective_samples: float, k: float) -> float:
    """D_n = 1 - exp(-k*n); n is already named for future n_eff support."""
    if not math.isfinite(effective_samples) or effective_samples <= 0:
        return 0.0
    return _unit(1.0 - math.exp(-k * effective_samples))


def topology_confidence(key: TopologyEvidenceKey) -> float:
    return TOPOLOGY_CONFIDENCE.get(key, TOPOLOGY_CONFIDENCE["unknown"])[0]


def composite_certainty(
    freshness_value: float,
    stability_value: float,
    density_value: float,
    topology_value: float,
    config: EWPSConfig,
) -> float:
    components = (
        _unit(freshness_value),
        _unit(stability_value),
        _unit(density_value),
        _unit(topology_value),
    )
    if config.certainty_mode == "product":
        return _unit(math.prod(components))
    weights = (
        config.weights.freshness,
        config.weights.stability,
        config.weights.density,
        config.weights.topology,
    )
    result = 1.0
    for component, weight in zip(components, weights, strict=True):
        if weight == 0:
            continue
        if component == 0:
            return 0.0
        result *= component**weight
    return _unit(result)


def raw_performance_cost(raw: RawMetrics, config: EWPSConfig) -> float | None:
    """Return the versioned v0.1 SLA-like cost, or None for missing evidence."""
    required = (
        (config.latency_weight, raw.latency_ms),
        (config.jitter_weight, raw.jitter_ms),
        (config.loss_weight, raw.loss_pct),
    )
    if not raw.reachable or raw.sample_count <= 0:
        return None
    if any(weight > 0 and value is None for weight, value in required):
        return None
    values = [value for _, value in required if value is not None]
    if any(not math.isfinite(value) or value < 0 for value in values):
        return None
    return (
        config.latency_weight * (raw.latency_ms or 0.0)
        + config.jitter_weight * (raw.jitter_ms or 0.0)
        + config.loss_weight * (raw.loss_pct or 0.0)
    )


def calculate_path(
    path_id: str,
    raw: RawMetrics,
    evidence: EvidenceInput,
    config: EWPSConfig,
) -> EWPSCalculation:
    ft = freshness(evidence.age_seconds, config.lambda_decay)
    sv = latency_stability(evidence.mean_ms, evidence.stddev_ms)
    dn = evidence_density(evidence.effective_samples, config.density_k)
    tc = topology_confidence(evidence.topology_evidence)
    certainty = composite_certainty(ft, sv, dn, tc, config)
    raw_cost = raw_performance_cost(raw, config)
    reasons: list[str] = []
    if raw_cost is None:
        reasons.append("missing_or_failed_telemetry")
    if evidence.mean_ms is None or evidence.mean_ms <= 0:
        reasons.append("invalid_latency_mean")
    if evidence.effective_samples <= 0:
        reasons.append("no_effective_samples")
    if tc <= 0:
        reasons.append("unusable_topology_evidence")
    if certainty < config.p_min:
        reasons.append("below_minimum_evidence")
    valid = raw_cost is not None
    eligible = valid and certainty >= config.p_min and certainty > 0.0
    ewps_cost: float | None = None
    if eligible and raw_cost is not None:
        penalty = 1.0 if config.alpha == 0 else certainty**config.alpha
        if penalty > 0:
            candidate = raw_cost / penalty
            ewps_cost = candidate if math.isfinite(candidate) else None
    if eligible and ewps_cost is None:
        eligible = False
        reasons.append("non_finite_cost")
    return EWPSCalculation(
        pathId=path_id,
        raw=raw,
        evidence=evidence,
        certainty=CertaintyComponents(
            freshness=ft,
            stability=sv,
            density=dn,
            topology=tc,
            composite=certainty,
        ),
        rawCost=raw_cost,
        ewpsCost=ewps_cost,
        eligible=eligible,
        valid=valid,
        reasons=reasons,
    )


def _choice(
    algorithm: str,
    calculations: Iterable[EWPSCalculation],
    key,
    reason: str,
) -> AlgorithmChoice:
    candidates = [item for item in calculations if key(item) is not None]
    if not candidates:
        return AlgorithmChoice(
            algorithm=algorithm,
            pathId=None,
            cost=None,
            reason="No path has sufficient telemetry for this strategy.",
        )
    selected = min(candidates, key=lambda item: (float(key(item)), item.path_id))
    return AlgorithmChoice(
        algorithm=algorithm,
        pathId=selected.path_id,
        cost=float(key(selected)),
        reason=reason,
    )


def comparison_choices(calculations: list[EWPSCalculation]) -> list[AlgorithmChoice]:
    latency = _choice(
        "lowest_latency",
        calculations,
        lambda item: item.raw.latency_ms if item.valid else None,
        "Lowest current valid mean latency.",
    )
    losses = [
        item
        for item in calculations
        if item.valid and item.raw.loss_pct is not None and item.raw.latency_ms is not None
    ]
    if not losses:
        loss = AlgorithmChoice(
            algorithm="lowest_loss",
            pathId=None,
            cost=None,
            reason="Packet loss is not meaningfully available.",
        )
    elif len({item.raw.loss_pct for item in losses}) == 1:
        selected = min(losses, key=lambda item: (item.raw.latency_ms or 0.0, item.path_id))
        loss = AlgorithmChoice(
            algorithm="lowest_loss",
            pathId=selected.path_id,
            cost=selected.raw.loss_pct,
            reason="Loss is tied, so current latency breaks the tie deterministically.",
        )
    else:
        selected = min(
            losses,
            key=lambda item: (item.raw.loss_pct or 0.0, item.raw.latency_ms or 0.0, item.path_id),
        )
        loss = AlgorithmChoice(
            algorithm="lowest_loss",
            pathId=selected.path_id,
            cost=selected.raw.loss_pct,
            reason="Lowest current valid packet loss; latency breaks ties.",
        )
    performance = _choice(
        "performance_only",
        calculations,
        lambda item: item.raw_cost if item.valid else None,
        "Lowest v0.1 SLA-like performance cost without uncertainty penalty.",
    )
    ewps = _choice(
        "ewps",
        calculations,
        lambda item: item.ewps_cost if item.eligible else None,
        "Lowest eligible evidence-weighted cost without hysteresis.",
    )
    return [latency, loss, performance, ewps]


@dataclass
class HysteresisState:
    current_path_id: str | None = None
    last_switch_at: datetime | None = None
    best_path_id: str | None = None
    best_since: datetime | None = None
    eligible_since: dict[str, datetime] = field(default_factory=dict)
    recovered_at: dict[str, datetime] = field(default_factory=dict)
    eligibility_seen: dict[str, bool] = field(default_factory=dict)

    def decide(
        self,
        timestamp: datetime,
        calculations: list[EWPSCalculation],
        config: EWPSConfig,
    ) -> HysteresisDecision:
        by_id = {item.path_id: item for item in calculations}
        for item in calculations:
            was_eligible = self.eligibility_seen.get(item.path_id)
            if item.eligible:
                if item.path_id not in self.eligible_since:
                    self.eligible_since[item.path_id] = timestamp
                if was_eligible is False:
                    self.recovered_at[item.path_id] = timestamp
            else:
                self.eligible_since.pop(item.path_id, None)
            self.eligibility_seen[item.path_id] = item.eligible

        eligible = [item for item in calculations if item.eligible and item.ewps_cost is not None]
        best = min(eligible, key=lambda item: (item.ewps_cost or 0.0, item.path_id)) if eligible else None
        best_id = best.path_id if best else None
        if best_id != self.best_path_id:
            self.best_path_id = best_id
            self.best_since = timestamp if best_id else None

        if best is None:
            previous = self.current_path_id
            self.current_path_id = None
            return HysteresisDecision(
                preferredPathId=None,
                recommendationChanged=previous is not None,
                suppressed=False,
                wouldSwitch=False,
                reason="No candidate path is eligible under the current evidence threshold.",
                switchBlockedBy="shadow_mode",
            )

        evidence_age = (timestamp - self.eligible_since.get(best.path_id, timestamp)).total_seconds()
        if self.current_path_id is None:
            if evidence_age < config.hysteresis.minimum_evidence_seconds:
                return HysteresisDecision(
                    preferredPathId=None,
                    challengerPathId=best.path_id,
                    suppressed=True,
                    wouldSwitch=False,
                    reason=(
                        f"Initial recommendation suppressed: eligible evidence duration "
                        f"{evidence_age:.1f}s is below {config.hysteresis.minimum_evidence_seconds:.1f}s."
                    ),
                    switchBlockedBy="minimum_evidence_duration",
                )
            self.current_path_id = best.path_id
            self.last_switch_at = timestamp
            return HysteresisDecision(
                preferredPathId=best.path_id,
                challengerPathId=best.path_id,
                recommendationChanged=True,
                suppressed=False,
                wouldSwitch=False,
                reason="Initial EWPS recommendation established after sufficient evidence duration.",
                switchBlockedBy="shadow_mode",
            )

        if best.path_id == self.current_path_id:
            return HysteresisDecision(
                preferredPathId=self.current_path_id,
                challengerPathId=best.path_id,
                reason="Current recommendation remains the lowest eligible EWPS cost.",
                switchBlockedBy="shadow_mode",
            )

        challenger = best
        current = by_id.get(self.current_path_id)
        blockers: list[tuple[str, str]] = []
        if evidence_age < config.hysteresis.minimum_evidence_seconds:
            blockers.append((
                "minimum_evidence_duration",
                f"challenger evidence duration {evidence_age:.1f}s is below "
                f"{config.hysteresis.minimum_evidence_seconds:.1f}s",
            ))
        dwell = (
            (timestamp - self.last_switch_at).total_seconds()
            if self.last_switch_at is not None
            else math.inf
        )
        if dwell < config.hysteresis.minimum_dwell_seconds:
            blockers.append((
                "minimum_dwell_time",
                f"current recommendation dwell {dwell:.1f}s is below "
                f"{config.hysteresis.minimum_dwell_seconds:.1f}s",
            ))
        recovery_at = self.recovered_at.get(challenger.path_id)
        recovery_age = (timestamp - recovery_at).total_seconds() if recovery_at else math.inf
        if recovery_age < config.hysteresis.recovery_hold_down_seconds:
            blockers.append((
                "recovery_hold_down",
                f"challenger recovery age {recovery_age:.1f}s is below "
                f"{config.hysteresis.recovery_hold_down_seconds:.1f}s",
            ))
        if current and current.eligible and current.ewps_cost is not None and current.ewps_cost > 0:
            improvement = (current.ewps_cost - (challenger.ewps_cost or 0.0)) / current.ewps_cost
            if improvement < config.hysteresis.minimum_improvement:
                blockers.append((
                    "minimum_improvement",
                    f"relative improvement {improvement:.3f} is below "
                    f"{config.hysteresis.minimum_improvement:.3f}",
                ))
        if blockers:
            code, detail = blockers[0]
            return HysteresisDecision(
                preferredPathId=self.current_path_id,
                challengerPathId=challenger.path_id,
                suppressed=True,
                wouldSwitch=False,
                reason=f"Recommendation suppressed by hysteresis: {detail}.",
                switchBlockedBy=code,
            )

        previous = self.current_path_id
        self.current_path_id = challenger.path_id
        self.last_switch_at = timestamp
        return HysteresisDecision(
            preferredPathId=challenger.path_id,
            challengerPathId=challenger.path_id,
            recommendationChanged=True,
            wouldSwitch=True,
            reason=f"Shadow recommendation changed from {previous} to {challenger.path_id}.",
            switchBlockedBy="shadow_mode",
        )


def deterministic_explanation(
    calculations: list[EWPSCalculation],
    hysteresis: HysteresisDecision,
    previous: dict[str, EWPSCalculation],
) -> str:
    by_id = {item.path_id: item for item in calculations}
    if hysteresis.suppressed:
        return hysteresis.reason
    preferred = by_id.get(hysteresis.preferred_path_id or "")
    if preferred is None:
        return "No EWPS recommendation is available because every candidate is ineligible or missing telemetry."
    if hysteresis.recommendation_changed:
        alternatives = [
            item for item in calculations
            if item.path_id != preferred.path_id and item.raw_cost is not None
        ]
        nominally_faster = min(alternatives, key=lambda item: (item.raw_cost or 0.0, item.path_id)) if alternatives else None
        if nominally_faster and nominally_faster.raw_cost is not None and preferred.raw_cost is not None and nominally_faster.raw_cost < preferred.raw_cost:
            prior = previous.get(nominally_faster.path_id)
            before = prior.certainty.composite if prior else nominally_faster.certainty.composite
            causes: list[str] = []
            labels = {
                "freshness": "current observations became stale",
                "stability": "latency variance increased",
                "density": "current observations were sparse",
                "topology": "topology evidence weakened",
            }
            if prior:
                for component, label in labels.items():
                    old = getattr(prior.certainty, component)
                    new = getattr(nominally_faster.certainty, component)
                    if new < old - 0.05:
                        causes.append(label)
            if not causes:
                component_values = {
                    component: getattr(nominally_faster.certainty, component)
                    for component in labels
                }
                weakest = min(component_values, key=lambda component: (component_values[component], component))
                if component_values[weakest] < 0.8:
                    causes.append(labels[weakest])
            cause_text = f" because {' and '.join(causes)}" if causes else ""
            return (
                f"{nominally_faster.path_id} remains nominally faster, but its evidence-confidence index "
                f"is {nominally_faster.certainty.composite:.3f} (previously {before:.3f}){cause_text}. "
                f"{preferred.path_id} now has the lower eligible evidence-weighted cost."
            )
        return (
            f"{preferred.path_id} is now preferred with evidence-confidence index "
            f"{preferred.certainty.composite:.3f} and EWPS cost {preferred.ewps_cost:.3f}."
        )
    return (
        f"{preferred.path_id} remains preferred: evidence-confidence index "
        f"{preferred.certainty.composite:.3f}, EWPS cost {preferred.ewps_cost:.3f}."
    )


def _events(
    calculations: list[EWPSCalculation],
    previous: dict[str, EWPSCalculation],
    hysteresis: HysteresisDecision,
) -> list[str]:
    events: list[str] = []
    for item in calculations:
        prior = previous.get(item.path_id)
        if item.raw.loss_pct and (not prior or not prior.raw.loss_pct):
            events.append(f"packet_loss_event:{item.path_id}")
        if prior:
            if prior.certainty.freshness >= 0.5 > item.certainty.freshness:
                events.append(f"telemetry_became_stale:{item.path_id}")
            if item.certainty.stability < prior.certainty.stability - 0.2:
                events.append(f"variance_spike:{item.path_id}")
            if prior.eligible and not item.eligible:
                events.append(f"path_became_ineligible:{item.path_id}")
            if not prior.eligible and item.eligible:
                events.append(f"evidence_recovery:{item.path_id}")
            if prior.evidence.topology_evidence != item.evidence.topology_evidence:
                events.append(f"topology_confidence_changed:{item.path_id}")
        if not item.valid:
            events.append(f"telemetry_failure:{item.path_id}")
    if hysteresis.recommendation_changed:
        events.append("ewps_recommendation_change")
    if hysteresis.suppressed:
        events.append(f"recommendation_suppressed:{hysteresis.switch_blocked_by}")
    return events


class EWPSDecisionEngine:
    """Stateful hysteresis wrapper around the pure, versioned calculations."""

    def __init__(self, config: EWPSConfig) -> None:
        self.config = config.model_copy(deep=True)
        self.hysteresis = HysteresisState()
        self.previous: dict[str, EWPSCalculation] = {}
        self.decision_index = 0

    def evaluate(
        self,
        timestamp: datetime,
        path_inputs: list[tuple[str, RawMetrics, EvidenceInput]],
    ) -> DecisionPoint:
        calculations = [
            calculate_path(path_id, raw, evidence, self.config)
            for path_id, raw, evidence in sorted(path_inputs, key=lambda value: value[0])
        ]
        algorithms = comparison_choices(calculations)
        hysteresis = self.hysteresis.decide(timestamp, calculations, self.config)
        hysteresis_choice = AlgorithmChoice(
            algorithm="ewps_hysteresis",
            pathId=hysteresis.preferred_path_id,
            cost=(
                next(
                    (item.ewps_cost for item in calculations if item.path_id == hysteresis.preferred_path_id),
                    None,
                )
            ),
            reason=hysteresis.reason,
        )
        algorithms.append(hysteresis_choice)
        events = _events(calculations, self.previous, hysteresis)
        explanation = deterministic_explanation(calculations, hysteresis, self.previous)
        point = DecisionPoint(
            timestamp=timestamp,
            decisionIndex=self.decision_index,
            calculations=calculations,
            algorithms=algorithms,
            hysteresis=hysteresis,
            events=events,
            explanation=explanation,
        )
        self.previous = {item.path_id: item for item in calculations}
        self.decision_index += 1
        return point
