"""Development-only deterministic resilience scenario runner.

This package is intentionally outside ``backend.app`` and is never registered
with the production FastAPI application.  It supplies synthetic normalized
evidence to production services; it does not reimplement their reasoning.
"""

from .catalog import load_catalog
from .runner import ResilienceScenarioRunner

__all__ = ["ResilienceScenarioRunner", "load_catalog"]
