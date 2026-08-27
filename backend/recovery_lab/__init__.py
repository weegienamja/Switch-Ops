"""SwitchOps Recovery Lab -- development-only Windows primitive validation.

This package exists to answer, with evidence, whether a temporary unicast
address is a safe recovery primitive. It is not the SwitchOps recovery
executor and must never become one by accident:

* nothing under ``backend.app`` imports it;
* it exposes no API route;
* the packaged sidecar runs unelevated, and the OS refuses the mutating IP
  Helper calls to unelevated callers.

The product remains planning-only. See ``README.md`` for what has been measured
and what still requires an elevated run.
"""

__all__ = ["harness", "journal", "safety", "windows_unicast"]
