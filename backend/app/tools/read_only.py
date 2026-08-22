"""Read-only operations that combine commands + parsers + audit."""
from __future__ import annotations

import time
from typing import Callable, TypeVar

from ..audit_store import get_audit_store
from ..command_registry import resolve_read_command
from ..switch_client import SwitchClient

T = TypeVar("T")


def run_and_audit(
    client: SwitchClient,
    *,
    symbol: str,
    actor: str = "system",
) -> str:
    """Run a single allowlisted read command and audit it."""
    command = resolve_read_command(symbol)
    start = time.monotonic()
    err_type: str | None = None
    err_msg: str | None = None
    output = ""
    try:
        output = client.run(symbol)
        success = True
    except Exception as exc:
        success = False
        err_type = type(exc).__name__
        err_msg = str(exc)
        raise
    finally:
        duration_ms = int((time.monotonic() - start) * 1000)
        get_audit_store().record(
            actor=actor,
            action=f"read:{symbol}",
            commands=[command],
            success=success,
            duration_ms=duration_ms,
            error_type=err_type,
            error_message=err_msg,
        )
    return output
