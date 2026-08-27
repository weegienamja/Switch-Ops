"""Explicit immutable phase provider used only by the Resilience Lab runner."""
from __future__ import annotations

from .models import ResilienceScenario, ScenarioPhase


class ScenarioOrderError(RuntimeError):
    pass


class ImmutableScenarioProvider:
    def __init__(self, scenario: ResilienceScenario) -> None:
        self._scenario = scenario.model_copy(deep=True)
        self._index = -1

    @property
    def scenario(self) -> ResilienceScenario:
        return self._scenario.model_copy(deep=True)

    @property
    def current(self) -> ScenarioPhase:
        if self._index < 0:
            raise ScenarioOrderError("No scenario phase has been advanced.")
        return self._scenario.phases[self._index].model_copy(deep=True)

    @property
    def complete(self) -> bool:
        return self._index == len(self._scenario.phases) - 1

    def advance(self, expected_phase_id: str | None = None) -> ScenarioPhase:
        next_index = self._index + 1
        if next_index >= len(self._scenario.phases):
            raise ScenarioOrderError("The scenario has no remaining phases.")
        phase = self._scenario.phases[next_index]
        if expected_phase_id is not None and phase.id != expected_phase_id:
            raise ScenarioOrderError(
                f"Expected phase {expected_phase_id!r}, next phase is {phase.id!r}."
            )
        self._index = next_index
        return phase.model_copy(deep=True)

    def reset(self) -> None:
        self._index = -1
