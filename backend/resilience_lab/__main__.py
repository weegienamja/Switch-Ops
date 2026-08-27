"""Developer entry point for the SwitchOps Resilience Lab.

JSON stays the default output because it is what CI consumes. `--summary`
exists because a failing scenario buried in a JSON line is hard to act on: it
names the scenario, phase, dimension, expectation, and what actually happened.
"""
from __future__ import annotations

import argparse
import sys

from .catalog import load_catalog, scenario_by_id
from .models import ResilienceScenario, ScenarioRunResult
from .runner import ResilienceScenarioRunner


def _is_recovery(scenario: ResilienceScenario) -> bool:
    """A scenario that carries the environment back to a working state.

    Detected from the phases rather than a hand-maintained list, so a new
    recovery arc is picked up without anyone remembering to register it.
    """
    if len(scenario.phases) < 3:
        return False
    degraded = any(
        phase.expected.management_diagnosis
        not in (None, "MANAGEMENT_PATH_HEALTHY")
        for phase in scenario.phases[:-1]
    )
    final = scenario.phases[-1].expected
    recovered = final.management_diagnosis == "MANAGEMENT_PATH_HEALTHY"
    reconciled = final.current_attachment is not None
    return (degraded and recovered) or reconciled


def _summarize(result: ScenarioRunResult) -> str:
    lines = [f"{result.status:<4} {result.scenario_id}"]
    if result.status == "FAIL":
        for phase in result.phases:
            for assertion in phase.assertions:
                if assertion.passed:
                    continue
                lines.append(
                    f"       phase={phase.phase_id} dimension={assertion.dimension}"
                )
                lines.append(f"         expected: {assertion.expectation}")
                lines.append(f"         actual:   {assertion.actual}")
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="python -m backend.resilience_lab",
        description="Run deterministic SwitchOps resilience scenarios.",
    )
    parser.add_argument(
        "scenario", nargs="?", help="Scenario ID; omit to run the catalogue."
    )
    parser.add_argument(
        "--list", action="store_true", help="List scenarios and exit without running."
    )
    parser.add_argument(
        "--recovery-only",
        action="store_true",
        help="Run only degradation-to-recovery scenarios.",
    )
    parser.add_argument(
        "--summary",
        action="store_true",
        help="Print human-readable results instead of JSON.",
    )
    args = parser.parse_args(argv)

    if args.scenario:
        scenarios = [scenario_by_id(args.scenario)]
    else:
        scenarios = load_catalog().scenarios
        if args.recovery_only:
            scenarios = [item for item in scenarios if _is_recovery(item)]

    if args.list:
        for scenario in scenarios:
            marker = "recovery" if _is_recovery(scenario) else "        "
            print(f"{marker}  {scenario.id:<45} {len(scenario.phases)} phases")
        print(
            f"\n{len(scenarios)} scenarios, "
            f"{sum(len(item.phases) for item in scenarios)} phases",
            file=sys.stderr,
        )
        return 0

    runner = ResilienceScenarioRunner()
    failed = False
    for scenario in scenarios:
        result = runner.run(scenario)
        failed = failed or result.status == "FAIL"
        print(_summarize(result) if args.summary else result.model_dump_json(by_alias=True))

    if args.summary:
        print(
            f"\n{len(scenarios)} scenarios, "
            f"{sum(len(item.phases) for item in scenarios)} phases, "
            f"{'FAILURES PRESENT' if failed else 'all passed'}",
            file=sys.stderr,
        )
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
